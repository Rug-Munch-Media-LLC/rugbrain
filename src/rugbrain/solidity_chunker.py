"""
Code-Aware Solidity Chunking - AST-based chunk boundaries.

Problem: Current chunking splits Solidity source at character boundaries (2500 chars).
This cuts functions in half, splits modifiers from their functions, and destroys
the semantic structure that makes contract security analysis effective.

Solution: Parse Solidity AST via solc, identify natural boundaries (contract,
function, modifier, event, error, struct, enum), and chunk at those points.
Preserves complete logical units for accurate security analysis.

Uses solc (Solidity compiler) already installed in the Docker container.
"""

import json
import logging
import re
import shutil
import subprocess

logger = logging.getLogger(__name__)

# ── AST node types that represent logical boundaries ────────────
CONTRACT_TYPES = {"ContractDefinition"}
FUNCTION_TYPES = {"FunctionDefinition"}
MODIFIER_TYPES = {"ModifierDefinition"}
EVENT_TYPES = {"EventDefinition"}
ERROR_TYPES = {"ErrorDefinition"}
STRUCT_TYPES = {"StructDefinition"}
ENUM_TYPES = {"EnumDefinition"}
STATE_VAR_TYPES = {"StateVariableDeclaration", "VariableDeclaration"}
IMPORT_TYPES = {"ImportDirective"}
PRAGMA_TYPES = {"PragmaDirective"}

# Minimum chunk size (don't create tiny chunks)
MIN_CHUNK_SIZE = 200
# Maximum chunk size (merge when possible)
TARGET_CHUNK_SIZE = 2000
MAX_CHUNK_SIZE = 3500


def parse_solidity_ast(source: str) -> dict | None:
    """Parse Solidity source into AST using solc.

    Returns the AST JSON or None if parsing fails.
    """
    try:
        # Use solc --ast-compact-json for the AST
        solc_bin = shutil.which("solc") or "solc"
        result = subprocess.run(  # noqa: S603 - source is piped via stdin, no shell interpretation
            [solc_bin, "--ast-compact-json", "--allow-paths", ".", "-"],
            input=source.encode(),
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.debug("solc AST parse failed: %s", result.stderr.decode()[:200])
            return None

        output = json.loads(result.stdout.decode())

        # solc output structure: {"sources": {"<stdin>": {"AST": {...}}}}
        for _source_name, source_data in output.get("sources", {}).items():
            return source_data.get("AST")

        return None
    except FileNotFoundError:
        logger.debug("solc not found - falling back to regex chunking")
        return None
    except json.JSONDecodeError:
        logger.debug("solc AST output not valid JSON")
        return None
    except Exception as e:
        logger.debug("solc AST parse error: %s", e)
        return None


def extract_boundaries(
    ast: dict,
    source: str,
) -> list[tuple[int, int, str]]:
    """
    Walk the Solidity AST and extract logical chunk boundaries.

    Returns list of (start_byte, end_byte, node_type) tuples sorted by position.
    Each tuple represents a complete, self-contained code unit.
    """
    boundaries = []
    source.split("\n")
    _compute_line_offsets(source)

    def _walk(node, depth=0):
        if not isinstance(node, dict):
            return

        node_type = node.get("nodeType", "")
        src_range = node.get("src", "")

        if src_range:
            parts = src_range.split(":")
            if len(parts) >= 2:
                start = int(parts[0])
                length = int(parts[1])
                end = start + length

                # Only capture top-level boundaries
                if node_type in CONTRACT_TYPES:
                    boundaries.append((start, end, "contract"))
                elif node_type in FUNCTION_TYPES and depth <= 2:
                    boundaries.append((start, end, "function"))
                elif node_type in MODIFIER_TYPES and depth <= 2:
                    boundaries.append((start, end, "modifier"))
                elif node_type in EVENT_TYPES and depth <= 2:
                    boundaries.append((start, end, "event"))
                elif node_type in ERROR_TYPES and depth <= 2:
                    boundaries.append((start, end, "error"))
                elif node_type in STRUCT_TYPES and depth <= 2:
                    boundaries.append((start, end, "struct"))
                elif node_type in ENUM_TYPES and depth <= 2:
                    boundaries.append((start, end, "enum"))

        # Recurse into children
        for key in (
            "nodes",
            "members",
            "body",
            "statements",
            "subNodes",
            "overrides",
            "modifiers",
            "parameters",
            "returnParameters",
            "baseContracts",
            "contractDependencies",
        ):
            children = node.get(key)
            if isinstance(children, list):
                for child in children:
                    _walk(child, depth + 1)
            elif isinstance(children, dict):
                _walk(children, depth + 1)

    _walk(ast)

    # Sort by position
    boundaries.sort(key=lambda b: b[0])

    return boundaries


def _compute_line_offsets(source: str) -> list[int]:
    """Compute byte offset of start of each line."""
    offsets = [0]
    for i, ch in enumerate(source):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def chunk_solidity_ast(
    source: str,
    target_size: int = TARGET_CHUNK_SIZE,
    max_size: int = MAX_CHUNK_SIZE,
    min_size: int = MIN_CHUNK_SIZE,
) -> list[str]:
    """
    Chunk Solidity source code using AST-aware boundaries.

    Falls back to regex-based chunking if solc AST parsing fails.
    Returns list of code chunks, each containing complete logical units.
    """
    ast = parse_solidity_ast(source)

    if ast:
        boundaries = extract_boundaries(ast, source)
        if boundaries:
            chunks = _build_chunks_from_boundaries(source, boundaries, target_size, max_size)
            logger.info("AST chunking: %s boundaries → %s chunks", len(boundaries), len(chunks))
            return chunks

    # Fallback: regex-based heuristic chunking for unparseable sources
    logger.debug("AST chunking unavailable - using regex heuristics")
    return _chunk_solidity_regex(source, target_size, max_size)


def _build_chunks_from_boundaries(
    source: str,
    boundaries: list[tuple[int, int, str]],
    target_size: int,
    max_size: int,
) -> list[str]:
    """Build chunks by grouping AST boundaries into target-sized units."""
    chunks = []
    current = ""

    for start, end, _btype in boundaries:
        segment = source[start:end]

        # If adding this segment would exceed max_size, start a new chunk
        if len(current) + len(segment) > max_size and current:
            chunks.append(current.strip())
            current = ""

        # If current + segment fits in target, add it
        if len(current) + len(segment) <= target_size or not current:
            current += segment + "\n\n"
        else:
            # Current is big enough - flush it
            chunks.append(current.strip())
            current = segment + "\n\n"

    # Don't forget the last chunk
    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if len(c) >= MIN_CHUNK_SIZE]


def _chunk_solidity_regex(
    source: str,
    target_size: int = TARGET_CHUNK_SIZE,
    max_size: int = MAX_CHUNK_SIZE,
) -> list[str]:
    """
    Regex-based Solidity chunking as fallback when AST parsing fails.

    Splits at:
      - function/constructor/modifier/receive/fallback boundaries
      - contract/interface/library boundaries
      - import/pragma statements (grouped together)
    """
    # Split at contract-level boundaries first
    contract_pattern = re.compile(
        r"(?=(?:abstract\s+)?(?:contract|interface|library)\s+\w+)",
        re.MULTILINE,
    )
    # Split at function boundaries within contracts
    func_pattern = re.compile(
        r"(?=(?:function|constructor|modifier|receive|fallback|event|error|struct|enum)\s+\w*)",
        re.MULTILINE,
    )

    # First pass: split by contracts
    blocks = []
    current = ""

    # Extract preamble (pragma + imports) first
    lines = source.split("\n")
    preamble = []
    body_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("pragma ", "import ")):
            preamble.append(line)
            body_start = i + 1
        elif stripped and not stripped.startswith("//") and not stripped.startswith("/*"):
            break
        else:
            body_start = i + 1

    body = "\n".join(lines[body_start:])

    # Chunk preamble together if small, otherwise split
    pre_text = "\n".join(preamble)
    if pre_text:
        blocks.append(pre_text)

    # Contract-level splitting
    if body:
        contracts = contract_pattern.split(body)
        if len(contracts) > 1:
            for contract in contracts:
                if not contract.strip():
                    continue
                # Further split by function boundaries within contract
                sub_blocks = func_pattern.split(contract)
                for block in sub_blocks:
                    if block.strip():
                        blocks.append(block.strip())
        else:
            # No contract boundaries found - split by functions only
            sub_blocks = func_pattern.split(body)
            for block in sub_blocks:
                if block.strip():
                    blocks.append(block.strip())

    # Group blocks into target-sized chunks
    chunks = []
    current = ""
    for block in blocks:
        if len(current) + len(block) > max_size and current:
            chunks.append(current.strip())
            current = ""
        if len(block) > max_size:
            # Block is too large - split at line boundaries
            chunk_lines = block.split("\n")
            sub = ""
            for line in chunk_lines:
                if len(sub) + len(line) > target_size and sub:
                    chunks.append(sub.strip())
                    sub = ""
                sub += line + "\n"
            if sub.strip():
                if current:
                    current += sub
                else:
                    chunks.append(sub.strip())
        else:
            current += block + "\n\n"

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]
