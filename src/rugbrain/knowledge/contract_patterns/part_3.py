"""Chunk 3 of ``SECURITY_PATTERNS`` (generated; literal data only).

Source: scripts/contract_knowledge_data.py
"""

ITEMS = [
    {
            "pattern_id": "evm:delegatecall-arbitrary",
            "title": "Delegatecall to attacker-controlled address",
            "ecosystem": "ethereum",
            "category": "access-control",
            "swc": "SWC-112",
            "severity": "critical",
            "vulnerable_code": (
                "function execute(address target, bytes calldata data) external {\n"
                "    target.delegatecall(data);  // runs in THIS contract's context\n"
                "}"
            ),
            "fixed_code": (
                "// Only allow pre-registered, trusted implementations and always\n"
                "// check the caller (e.g. onlyOwner) before any delegatecall.\n"
                "function upgrade(address newImpl) external onlyOwner {\n"
                '    require(isTrustedImpl[newImpl], "untrusted impl");\n'
                "    _setImplementation(newImpl);\n"
                "}"
            ),
            "description": (
                "delegatecall executes callee code in the caller's storage/context. "
                "If an attacker can influence the target, they can overwrite "
                "owner/slot0 and take over the contract. The Parity multi-sig "
                "freeze ($280M) was a delegatecall selfdestruct."
            ),
            "detection_heuristics": [
                "delegatecall with a target derived from user input",
                "proxy upgrade functions without whitelist/timelock",
            ],
            "references": [
                "https://swcregistry.io/docs/SWC-112",
                "https://www.parity.io/blog/what-happened",
            ],
        },
    {
            "pattern_id": "evm:selfdestruct",
            "title": "selfdestruct / contract destruction",
            "ecosystem": "ethereum",
            "category": "access-control",
            "swc": "SWC-106",
            "severity": "critical",
            "vulnerable_code": (
                "function kill() external {\n"
                "    selfdestruct(payable(owner));  // if reachable by attacker: catastrophic\n"
                "}"
            ),
            "fixed_code": (
                "// Remove selfdestruct entirely; or gate behind a multisig + timelock\n"
                "// with a long delay and keep an emergency pause separate."
            ),
            "description": (
                "selfdestruct sends the contract's entire balance and removes code. "
                "If callable by anyone (missing access control, or reachable via a "
                "delegatecall to a library containing it), funds and functionality "
                "are destroyed."
            ),
            "detection_heuristics": [
                "selfdestruct in any reachable code path",
                "SUICIDE opcode in bytecode",
            ],
            "references": ["https://swcregistry.io/docs/SWC-106"],
        },
    {
            "pattern_id": "evm:uninitialized-proxy",
            "title": "Uninitialized upgradeable proxy / missing initializer guard",
            "ecosystem": "ethereum",
            "category": "access-control",
            "swc": "SWC-109",
            "severity": "critical",
            "vulnerable_code": (
                "// Proxy can be initialized by anyone before the deployer does\n"
                "contract Upgradeable is Initializable {\n"
                "    function initialize(address _owner) public initializer {\n"
                "        owner = _owner;\n"
                "    }\n"
                "}"
            ),
            "fixed_code": (
                "// Call initialize() in the same tx as deployment (deploy-and-call),\n"
                "// or use a constructor guard that blocks all post-deploy calls.\n"
                "constructor() { _disableInitializers(); }"
            ),
            "description": (
                "If an upgradeable contract's initializer is not protected (and not "
                "called atomically at deploy), anyone can call it first and become "
                "owner/admin of the proxy."
            ),
            "detection_heuristics": [
                "public initialize() without initializer guard in an upgradeable context",
                "proxy deployed without atomic constructor+initialize",
            ],
            "references": ["https://swcregistry.io/docs/SWC-109"],
        },
    {
            "pattern_id": "evm:signature-replay",
            "title": "Signature replay / missing nonce",
            "ecosystem": "ethereum",
            "category": "signature-malleability",
            "swc": "SWC-121",
            "severity": "high",
            "vulnerable_code": (
                "function permit(address owner, address spender, uint256 value,\n"
                "                 uint256 deadline, bytes calldata sig) external {\n"
                "    // no nonce, no deadline expiry enforcement -> replayable\n"
                "    address recovered = ecrecover(hash(owner, spender, value), sig);\n"
                "    require(recovered == owner);\n"
                "    allowance[owner][spender] = value;\n"
                "}"
            ),
            "fixed_code": (
                "// Include a per-user nonce + domain separator + deadline, and\n"
                "// increment the nonce on use so the signature is single-use.\n"
                "function permit(...) external {\n"
                '    require(block.timestamp <= deadline, "expired");\n'
                "    uint256 nonce = nonces[owner]++;\n"
                '    bytes32 digest = keccak256(abi.encodePacked("\\x19\\x01", DOMAIN_SEPARATOR, hash(owner, spender, value, nonce, deadline)));\n'
                '    require(ecrecover(digest, sig) == owner, "bad sig");\n'
                "    allowance[owner][spender] = value;\n"
                "}"
            ),
            "description": (
                "Signatures that lack a nonce/domain/expiry can be replayed across "
                "chains, forks, or multiple times. Always use EIP-712 with nonces "
                "and deadlines."
            ),
            "detection_heuristics": [
                "ecrecover without nonce/domain separator",
                "no deadline check on signed approvals",
            ],
            "references": [
                "https://swcregistry.io/docs/SWC-121",
                "https://swcregistry.io/docs/SWC-122",
            ],
        },
    {
            "pattern_id": "evm:signature-malleability",
            "title": "ECDSA signature malleability (ecrecover)",
            "ecosystem": "ethereum",
            "category": "signature-malleability",
            "swc": "SWC-117",
            "severity": "medium",
            "vulnerable_code": (
                "function recoverSigner(bytes32 digest, bytes calldata sig) external pure returns (address) {\n"
                "    return ecrecover(digest, uint8(sig[64]), uint256(bytes32(sig[0:32])), uint256(bytes32(sig[32:64])));\n"
                "}"
            ),
            "fixed_code": (
                "// Use OpenZeppelin ECDSA.recover which enforces low-s + malleability\n"
                "// checks, and validate against expected signer.\n"
                "address signer = ECDSA.recover(digest, sig);"
            ),
            "description": (
                "The same ECDSA signature can exist in two valid (s, 27/28) forms. "
                "If a protocol keyed state on the raw signature, an attacker could "
                "submit the malleated variant to bypass dedup checks."
            ),
            "detection_heuristics": [
                "raw ecrecover without OpenZeppelin ECDSA wrapper",
                "signature used as a map key",
            ],
            "references": ["https://swcregistry.io/docs/SWC-117"],
        },
]
