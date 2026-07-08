#!/usr/bin/env python3
"""
RAG Tool Interface for OpenClaw Agents (2026 Agentic RAG Approach)
- Exposes RAG as a tool callable via Hermes subagents
- Supports reflection loops and multi-hop retrieval
- Integrates with OpenClaw tool schema
"""

import json
import sys
from pathlib import Path
from typing import Any

# Add RAG lib to path
sys.path.insert(0, str(Path.home() / ".hermes" / "scripts"))
from rag_system_v2 import RMIRAGSystem


class RagToolInterface:
    """2026 Agentic RAG tool wrapper."""

    def __init__(self):
        self.rag = RMIRAGSystem()
        self.max_iterations = 3  # Prevent infinite loops

    def query(
        self,
        query: str,
        namespace: str | None = None,
        n_results: int = 5,
        min_confidence: float = 0.7,
    ) -> dict[str, Any]:
        """
        Query RAG and return results with reflection.

        Implements 2026 Agentic RAG pattern:
        1. Initial retrieval
        2. Self-evaluation (confidence check)
        3. Refinement if needed
        4. Evidence-weighted synthesis
        """
        # Step 1: Initial retrieval
        results = self.rag.query(query, n=n_results, source_filter=namespace)

        # Convert ChromaDB distance to confidence score
        def distance_to_confidence(distance: float) -> float:
            """ChromaDB distance → confidence (lower distance = higher confidence)."""
            if distance is None:
                return 0.5
            return max(0.0, 1.0 - distance)

        # Convert results to standard format
        formatted_results = []
        for r in results:
            conf = distance_to_confidence(r.get("distance"))
            if conf >= min_confidence:
                formatted_results.append(
                    {
                        "text": r["text"],
                        "metadata": r["metadata"],
                        "confidence": round(conf, 3),
                        "source": r["metadata"].get("source", "unknown"),
                    }
                )

        # Step 2: Self-evaluation
        confidence_score = len(formatted_results) / n_results  # heuristic

        output = {
            "query": query,
            "results": formatted_results,
            "confidence": round(confidence_score, 3),
            "n_retrieved": len(formatted_results),
            "metadata": self.rag.get_stats(),
        }

        # Step 3: Reflection loop if confidence low
        if confidence_score < 0.6:
            output["reflection"] = {
                "step": 1,
                "original_confidence": confidence_score,
                "threshold": 0.6,
                "refined_query": f"additional context for: {query}",
                "action": "refining",
            }
            # Try refinement (single additional query)
            refined_results = self.rag.query(output["reflection"]["refined_query"], n=n_results)
            for r in refined_results:
                conf = distance_to_confidence(r.get("distance"))
                if conf >= min_confidence:
                    formatted_results.append(
                        {
                            "text": r["text"],
                            "metadata": r["metadata"],
                            "confidence": round(conf, 3),
                            "source": r["metadata"].get("source", "unknown"),
                        }
                    )
            output["results"] = formatted_results
            output["n_refined"] = len(refined_results)

        return output

    def multi_hop_query(self, question: str, hops: list[dict[str, str]]) -> dict[str, Any]:
        """
        Multi-hop retrieval: chain multiple queries.

        Example:
        hops = [
            {"purpose": "finding token patterns", "query": "rug pull token patterns"},
            {"purpose": "scam indicators", "query": " scam indicators from recent rugs"}
        ]
        """
        all_results = []

        for hop in hops:
            hop_results = self.query(hop["query"])
            hop_result_list = [
                {"text": r["text"], "source": r["source"], "hop": hop["purpose"]} for r in hop_results["results"]
            ]
            all_results.extend(hop_result_list)

        return {
            "question": question,
            "hops": len(hops),
            "results": all_results,
            "total_results": len(all_results),
        }

    def reflection_loop(self, original_query: str, max_iterations: int = 3) -> dict[str, Any]:
        """
        Self-correcting retrieval with iteration budgets.

        Stops when:
        - Confidence threshold reached (0.8)
        - Max iterations exceeded
        - No new results found
        """
        iterations = []
        current_query = original_query
        all_results = []

        for i in range(max_iterations):
            result = self.query(current_query, n_results=5)

            iterations.append(
                {
                    "step": i,
                    "query": current_query,
                    "results_count": len(result["results"]),
                    "confidence": result["confidence"],
                }
            )

            # Add new results
            all_results.extend(result["results"])

            # Check stop conditions
            if result["confidence"] >= 0.8:
                iterations[-1]["reason"] = "confidence_threshold_reached"
                break

            if len(result["results"]) == 0:
                iterations[-1]["reason"] = "no_new_results"
                break

            # Refine query for next iteration
            current_query = f"details about: {current_query}"

        return {
            "original_query": original_query,
            "iterations": iterations,
            "final_results": all_results,
        }

    def get_tool_schema(self) -> dict[str, Any]:
        """Return OpenAI-compatible tool schema for agents."""
        return {
            "type": "function",
            "function": {
                "name": "rag_query",
                "description": (
                    "Query RugMunch Intelligence RAG for crypto security patterns, "
                    "rug pull indicators, wallet risk scores, and market intelligence."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": 'Search query for RAG (e.g., "rug pull patterns", "wallet risk indicators")',
                        },
                        "namespace": {
                            "type": "string",
                            "enum": [
                                "scan_results",
                                "alerts",
                                "content",
                                "market_data",
                                "onchain",
                                "social_feed",
                                "news",
                            ],
                            "default": None,
                            "description": "Optional source filter to narrow search",
                        },
                        "n_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 20,
                            "default": 5,
                            "description": "Number of results to retrieve",
                        },
                    },
                    "required": ["query"],
                },
            },
        }


# ============================================================
# CLI INTERFACE
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Tool Interface for OpenClaw Agents")
    parser.add_argument("command", choices=["query", "reflection", "multi-hop", "schema"])
    parser.add_argument("--query", type=str, help="Query text")
    parser.add_argument("--namespace", type=str, help="Source namespace filter")
    parser.add_argument("--n", type=int, default=5, help="Number of results")
    parser.add_argument("--hops", type=str, help="Multi-hop config (JSON)")
    parser.add_argument("--max-iter", type=int, default=3, help="Max reflection iterations")

    args = parser.parse_args()

    rag = RagToolInterface()

    if args.command == "query":
        result = rag.query(args.query, namespace=args.namespace, n_results=args.n)
        print(json.dumps(result, indent=2))

    elif args.command == "reflection":
        result = rag.reflection_loop(args.query, max_iterations=args.max_iter)
        print(json.dumps(result, indent=2))

    elif args.command == "multi-hop":
        hops = json.loads(args.hops)
        result = rag.multi_hop_query(args.query, hops)
        print(json.dumps(result, indent=2))

    elif args.command == "schema":
        print(json.dumps(rag.get_tool_schema(), indent=2))
