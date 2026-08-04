#!/usr/bin/env python3
"""Curated cryptography knowledge base for ``ingest_cryptography.py``.

Covers the topics RugBrain needs to answer applied-cryptography questions
in a Web3 context:

  * Cryptographic standards      — ECDSA, Ed25519, BLS signatures,
                                   Schnorr, secp256k1, P-256
  * Key management               — MPC, Shamir's Secret Sharing, hardware
                                   wallets, HD wallets, BIP-39/32
  * Hashing                      — keccak256, sha256, sha3, blake2b,
                                   pedersen, poseidon, mimc
  * Commitment schemes           — Merkle trees, Verkle trees, KZG /
                                   polynomial commitments
  * Zero-knowledge               — circuits, Groth16, Plonk, Halo2, Nova,
                                   zk-SNARKs, zk-STARKs, bulletproofs
  * Post-quantum                 — lattice-based (Kyber/Dilithium),
                                   hash-based (XMSS/LMS/SPHINCS+)

Each entry carries ``rel_key``, ``title``, ``category``, ``text`` and
optional ``references``. Sources: cryptopals, cryptohack, ZK docs
(zk-learning), ethereum.org, a16z crypto canon, Ristretto/zk-SNARK
literature, NIST FIPS 186-5 / SP 800-208.
"""

from __future__ import annotations

from typing import Any

CRYPTOGRAPHY_KNOWLEDGE: list[dict[str, Any]] = [
    # ═══════════════════════════════════════════════════════════════════
    # Cryptographic standards
    # ═══════════════════════════════════════════════════════════════════
    {
        "rel_key": "crypto:std:ecdsa",
        "title": "ECDSA — Elliptic Curve Digital Signature Algorithm",
        "category": "digital-signatures",
        "text": (
            "ECDSA (Elliptic Curve Digital Signature Algorithm) is the "
            "signature scheme used by Ethereum, Bitcoin, and most EVM chains "
            "on the secp256k1 curve. It signs a hash of the message: the "
            "signer picks a random nonce k, computes R = k*G, and derives "
            "s = k^-1 (z + r*d) mod n where z is the message hash, r = R.x, "
            "and d is the private key. The signature is (r, s). Key "
            "properties: 64-byte signatures (r||s) in Ethereum, malleable "
            "(flipping s to n-s is valid) — EIP-2 standardized low-s to "
            "prevent malleability. Critical pitfalls: nonce reuse leaks the "
            "private key (the 2010 PS3 hack; the 2013 Android SecureRandom "
            "bug), and using biased nonces enables lattice attacks "
            "(RFC 6979 deterministic nonces fix this). ECDSA is not "
            "threshold-friendly without extra protocols, which is one reason "
            "BLS exists."
        ),
        "references": ["https://en.wikipedia.org/wiki/Elliptic_Curve_Digital_Signature_Algorithm"],
    },
    {
        "rel_key": "crypto:std:ed25519",
        "title": "Ed25519 — EdDSA on Curve25519",
        "category": "digital-signatures",
        "text": (
            "Ed25519 is EdDSA instantiated on the Edwards-curve Curve25519 "
            "(Edwards25519). It is used by Solana, Stellar, Cardano, and "
            "many other chains because it is fast, deterministic, and "
            "side-channel resistant. Signatures are 64 bytes. Ed25519 is "
            "deterministic: the nonce is derived from the private key and "
            "message (SHA-512), eliminating the nonce-reuse class of "
            "vulnerabilities that plague ECDSA. Verification requires "
            "canonical encoding checks (RFC 8032) to avoid signature "
            "malleability. Variants: Ed25519ph (pre-hashed), Ed25519ctx "
            "(domain separation). Solana uses ed25519 for both account "
            "ownership and transaction signing. In smart-contract contexts, "
            "on-chain Ed25519 verification is expensive — Solana precompiles "
            "it, while EVM requires a precompile or ZK proof."
        ),
        "references": ["https://datatracker.ietf.org/doc/html/rfc8032"],
    },
    {
        "rel_key": "crypto:std:bls",
        "title": "BLS Signatures — Boneh-Lynn-Shacham",
        "category": "digital-signatures",
        "text": (
            "BLS signatures (Boneh-Lynn-Shacham) sign a hash to a point in a "
            "pairing-friendly group: signature = H(m)^sk, verify with "
            "e(sig, G2) == e(H(m), pk). The superpower is aggregation: many "
            "signatures on the same message aggregate into one short "
            "signature, and public keys aggregate too — this powers Ethereum "
            "2.0 consensus (BLS12-381), Danksharding, and threshold "
            "signatures. Security relies on the co-CDH assumption in pairing "
            "groups. Drawbacks: pairings are slow, and aggregation requires "
            "the same message — rogue-key attacks (RogueKey) are mitigated "
            "with proof-of-possession (PoP). Used in eth2 deposit/withdrawal "
            "credentials, and in some MPC threshold schemes (e.g. "
            "Chainlink's BLS threshold signatures)."
        ),
        "references": ["https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-bls-signature"],
    },
    {
        "rel_key": "crypto:std:schnorr",
        "title": "Schnorr Signatures",
        "category": "digital-signatures",
        "text": (
            "Schnorr signatures (the modern form used in Bitcoin's Taproot "
            "and BIP-340) are simple, linear, and provably secure under the "
            "DL assumption in the random oracle model. A Schnorr signature "
            "is (R, s) where R = k*G and s = k + e*d with e = H(R || m). "
            "Schnorr's linearity enables signature aggregation, "
            "MuSig/MuSig2 multi-signatures, and threshold schemes without "
            "pairings. BIP-340 defines Schnorr for secp256k1 in Bitcoin, "
            "with x-only public keys. Unlike ECDSA, Schnorr is "
            "non-malleable by construction and easier to prove secure. "
            "Solana and Ethereum do not use Schnorr natively, but ZK "
            "circuits and threshold wallets often do."
        ),
        "references": ["https://bips.dev/340/"],
    },
    {
        "rel_key": "crypto:std:secp256k1",
        "title": "secp256k1 Curve",
        "category": "digital-signatures",
        "text": (
            "secp256k1 is the Koblitz curve used by Bitcoin and Ethereum: "
            "y^2 = x^3 + 7 over the prime field p = 2^256 - 2^32 - 977, "
            "with generator G and group order n ≈ 2^256. Its Koblitz "
            "structure permits efficient endomorphism-based point "
            "multiplication (GLV method). The curve is not NIST-curve "
            "standard, which some prefer to avoid backdoor concerns; "
            "others criticize its smaller security margin vs P-256. "
            "Ethereum addresses are keccak256(pubkey)[-20:]. ECDSA "
            "signatures on secp256k1 are verified on-chain by the ecrecover "
            "precompile, which returns the recovered address. ZK circuits "
            "for secp256k1 are notoriously expensive (big-integer "
            "arithmetic), motivating KZG/BN254 for zk-rollups."
        ),
        "references": ["https://www.secg.org/sec2-v2.pdf"],
    },
    {
        "rel_key": "crypto:std:zk-snarks",
        "title": "zk-SNARKs — Succinct Non-interactive Arguments of Knowledge",
        "category": "zero-knowledge",
        "text": (
            "zk-SNARKs (zero-knowledge Succinct Non-interactive Arguments of "
            "Knowledge) let a prover convince a verifier that a statement is "
            "true without revealing the witness, with constant-size proofs "
            "and fast verification. Setup: a computation is compiled into an "
            "arithmetic circuit and a Quadratic Arithmetic Program (QAP); "
            "proving systems produce a proof from the circuit, the public "
            "inputs, and the private witness. The classic SNARK pipeline: "
            "circuit → R1CS → QAP → Groth16 proof. Trusted setup is "
            "required for many schemes (a toxic-waste ceremony); "
            "transparent setups (STARKs, Bulletproofs) avoid it. Used by "
            "zk-rollups (zkSync, Scroll), privacy mixers (Tornado Cash), "
            "and identity systems. Security notions: knowledge soundness, "
            "zero-knowledge, and completeness."
        ),
        "references": ["https://ethereum.org/en/zero-knowledge-proofs/"],
    },
    {
        "rel_key": "crypto:std:zk-starks",
        "title": "zk-STARKs — Scalable Transparent ARguments of Knowledge",
        "category": "zero-knowledge",
        "text": (
            "zk-STARKs (Scalable Transparent ARguments of Knowledge) are "
            "zero-knowledge proofs with no trusted setup: they rely only on "
            "collision-resistant hashing and the Fiat-Shamir transform. "
            "Proving is O(n log n) via fast Reed-Solomon proximity testing "
            "(FRI protocol). Proofs are larger than SNARKs (hundreds of KB) "
            "and verification is slower, but they are quantum-resistant "
            "(hash-based) and transparent. StarkWare's Cairo and the "
            "StarkEx/Starknet rollups are the flagship deployments. "
            "STARKs are well suited to CPU/general-purpose recursion and "
            "proving large state transitions."
        ),
        "references": ["https://starkware.co/stark/"],
    },
    {
        "rel_key": "crypto:std:groth16",
        "title": "Groth16 — Proving System",
        "category": "proving-systems",
        "text": (
            "Groth16 (Jens Groth, 2016) is the most efficient general-purpose "
            "zk-SNARK proving system: proofs are 3 group elements (~128 "
            "bytes on BN254), with verification requiring one pairing check. "
            "It requires a circuit-specific trusted setup (the powers-of-tau "
            "ceremony) and produces a unique proof per statement. Groth16 "
            "is used by Tornado Cash, zkSync Era, and most early zk-rollups. "
            "Setup phases: Phase 1 (universal powers of tau) and Phase 2 "
            "(circuit-specific). Soundness depends on the algebraic "
            "knowledge assumption and the toxicity of the setup secret. "
            "Groth16 is not updatable — a new circuit requires a new Phase 2."
        ),
        "references": ["https://eprint.iacr.org/2016/260"],
    },
    {
        "rel_key": "crypto:std:plonk",
        "title": "PLONK — Permutations over Lagrange-bases for Oecumenical Noninteractive arguments of Knowledge",
        "category": "proving-systems",
        "text": (
            "PLONK (2021) is a universal, updatable zk-SNARK: a single "
            "universal trusted setup (one powers-of-tau) serves all circuits "
            "up to a size bound, and new circuits need no new ceremony. It "
            "uses a permutation argument over Lagrange bases to enforce "
            "wiring, represented as polynomial identities checked with "
            "KZG polynomial commitments. Proofs are ~2-4 group elements and "
            "verification is a small number of pairings. PLONK's "
            "custom-gate design allows cheap app-specific constraints, "
            "which is why it underpins Aztec, Mina, and many zk-EVMs. "
            "Variants: Plonkish, UltraPLONK, Plonkup (lookup tables)."
        ),
        "references": ["https://eprint.iacr.org/2019/953"],
    },
    {
        "rel_key": "crypto:std:hallo2",
        "title": "Halo 2 — Recursive Zero-Knowledge Proofs",
        "category": "proving-systems",
        "text": (
            "Halo 2 (Electric Coin Company, 2021) is a recursive zk-SNARK "
            "that removes the trusted setup entirely. It uses inner product "
            "arguments (IPA) rather than pairings, making proofs larger but "
            "transparent. Halo 2's key feature is accumulation-based "
            "recursion: proofs verify other proofs in a cycle of elliptic "
            "curves (Pasta curves), enabling unbounded recursion without "
            "pairings. It is used by Zcash (Orchard), Scroll, and various "
            "zk-rollup provers. It introduces PLONKish circuits and "
            "custom lookup tables. Trade-off: verification is more "
            "expensive than Groth16, but there is no trusted setup and "
            "recursion is native."
        ),
        "references": ["https://zcash.github.io/halo2/"],
    },
    {
        "rel_key": "crypto:std:nova",
        "title": "Nova — Folding Scheme for Incremental Verifiable Computation",
        "category": "proving-systems",
        "text": (
            "Nova (Kothapalli, Setty, Tzialla, 2021) is a folding scheme for "
            "incremental verifiable computation (IVC): it folds two "
            "instances of an R1CS into one, enabling efficient recursive "
            "proofs of long computation chains without recursion in the "
            "proving system itself. Nova's proofs are small and verification "
            "is cheap; it is ideal for proving many sequential steps "
            "(e.g. rollup batches, virtual machines). Setty's 'CycleFold' "
            "and 'HyperNova' extend it. Nova is used by several zkVM "
            "projects and by the 'Mina' style verifiable computation. "
            "Because it targets R1CS, it pairs well with Circom circuits."
        ),
        "references": ["https://eprint.iacr.org/2021/370"],
    },
    {
        "rel_key": "crypto:std:bulletproofs",
        "title": "Bulletproofs — Range Proofs",
        "category": "proving-systems",
        "text": (
            "Bulletproofs (Bünz et al., 2018) are short non-interactive "
            "range proofs with no trusted setup, built from inner-product "
            "arguments. Proof size is O(log n) group elements. They were "
            "designed for confidential transactions (Monero) and "
            "range-checking committed amounts. Verification is linear-time "
            "but batched verification is efficient. Bulletproofs are also "
            "used in zk-proof systems as a fallback when pairing-friendly "
            "curves are unavailable. Their log-sized proofs make them "
            "practical on-chain for privacy-preserving balance checks."
        ),
        "references": ["https://eprint.iacr.org/2017/1066"],
    },
    {
        "rel_key": "crypto:std:circom",
        "title": "Circom — ZK Circuit Compiler",
        "category": "circuits",
        "text": (
            "Circom (iden3) is the de-facto zk-circuit language: it compiles "
            "high-level constraint code into R1CS that Groth16/PLONK "
            "proving systems consume. Circom circuits are written in a "
            "domain-specific language with signals and templates; the "
            "compiler outputs .r1cs and a witness generator (wasm/C++). "
            "Security lessons: signals must be range-checked (circomlib's "
            "Num2Bits), and 'unconstrained' signals allow witness forgery. "
            "Common audit findings in Circom circuits: missing range checks, "
            "incorrect signal aliasing, and unsafe use of public inputs. "
            "Circom 2 is the modern version; circomlib provides battle-"
            "tested templates (Merkle proof verification, poseidon hash, "
            "EdDSA)."
        ),
        "references": ["https://docs.circom.io/"],
    },
    # ═══════════════════════════════════════════════════════════════════
    # Key management
    # ═══════════════════════════════════════════════════════════════════
    {
        "rel_key": "crypto:key:mpc",
        "title": "MPC — Multi-Party Computation for Key Management",
        "category": "key-management",
        "text": (
            "MPC (multi-party computation) splits a private key into shares "
            "distributed across parties, and signatures are computed "
            "cooperatively without ever reconstructing the key. Threshold "
            "signature schemes (TSS) such as GG18/GG20 (ECDSA), FROST "
            "(Schnorr), and BLS threshold make it so any t-of-n parties can "
            "sign. MPC wallets (Fireblocks, ZenGo, Coinbase) protect against "
            "single-point key theft: no one party holds the key. Attack "
            "surface: malicious parties can bias nonces, leak shares via "
            "side channels, or force abort; formal proofs (UC security) and "
            "KeyGen/Sign protocols mitigate. MPC is distinct from Shamir "
            "secret sharing: Shamir reconstructs the key, MPC signs without "
            "reconstruction."
        ),
        "references": ["https://en.wikipedia.org/wiki/Secure_multi-party_computation"],
    },
    {
        "rel_key": "crypto:key:shamir",
        "title": "Shamir's Secret Sharing (SSS)",
        "category": "key-management",
        "text": (
            "Shamir's Secret Sharing splits a secret s into n shares such "
            "that any t shares reconstruct s but t-1 reveal nothing. "
            "Construction: pick a random polynomial f of degree t-1 with "
            "f(0)=s; each share is a point (i, f(i)). Reconstruction uses "
            "Lagrange interpolation. Used in: multisig backups, "
            "SLIP-39 (advanced BIP-39 backup), seed phrase splitting, "
            "and cold-storage custody. Caveats: shares are equivalent to "
            "the key (loss of t shares loses the secret); no security "
            "against a malicious share-holder altering shares without "
            "verification — 'verifiable secret sharing' (Feldman's VSS) "
            "adds commitments. Threshold cryptosystems (TSS) extend SSS "
            "to signing without reconstruction."
        ),
        "references": ["https://en.wikipedia.org/wiki/Shamir%27s_secret_sharing"],
    },
    {
        "rel_key": "crypto:key:hardware-wallets",
        "title": "Hardware Wallets — Secure Enclaves for Keys",
        "category": "key-management",
        "text": (
            "Hardware wallets (Ledger, Trezor, SafePal, Keystone) store "
            "private keys in a secure element and sign transactions without "
            "exposing the key to the host machine. They generate a seed "
            "(BIP-39 12/24 words) and derive keys via BIP-32/44 HD paths. "
            "Security model: PIN + passphrase protects the seed; the device "
            "is air-gapped or uses secure USB/BLE; the secure element "
            "resists physical extraction. Attack surface: supply-chain "
            "tampering, malicious display/UX attacks, firmware bugs, and "
            "phishing that signs malicious payloads (blind signing). "
            "EIP-712 typed-data signing and 'display verification' reduce "
            "blind-signing risk. Best practice: verify addresses on-device, "
            "use passphrase, and maintain a hardware backup."
        ),
        "references": ["https://en.wikipedia.org/wiki/Cryptocurrency_hardware_wallet"],
    },
    {
        "rel_key": "crypto:key:hd-wallets",
        "title": "HD Wallets — BIP-32/BIP-39/BIP-44",
        "category": "key-management",
        "text": (
            "Hierarchical Deterministic (HD) wallets derive an unlimited "
            "tree of keys from one seed: BIP-32 defines hardened and "
            "non-hardened child derivation from extended public/private "
            "keys; BIP-39 maps 128-256 bits of entropy to 12/24 mnemonic "
            "words with a checksum; BIP-44 defines the path m/44'/coin'/"
            "account'/change/index. Non-hardened derivation (m/0) allows "
            "public-key-only child derivation (watch-only wallets), but "
            "leaking a child private key plus the parent public key "
            "compromises the whole non-hardened branch. Path prefixes "
            "standardize coin selection: 44'=BIP44 legacy, 49'=segwit "
            "P2SH, 84'=bech32, 60'=Ethereum. Solana uses a BIP-44-like "
            "path m/44'/501'."
        ),
        "references": ["https://github.com/bitcoin/bips/blob/master/bip-0032.mediawiki"],
    },
    {
        "rel_key": "crypto:key:bip39",
        "title": "BIP-39 Mnemonics",
        "category": "key-management",
        "text": (
            "BIP-39 encodes a wallet seed as a human-readable mnemonic: "
            "128-256 bits of entropy map to 12, 15, 18, 21, or 24 words "
            "from a 2048-word list, with a checksum (entropy/32 bits) "
            "appended. The mnemonic is converted to a 512-bit seed via "
            "PBKDF2-HMAC-SHA512 with 2048 iterations and a passphrase "
            "(often empty; a passphrase adds a hidden '13th/25th word'). "
            "Security notes: the mnemonic IS the key — anyone who obtains "
            "it controls the funds; a strong passphrase protects against "
            "physical theft of the words; backups should be offline and "
            "redundant. SLIP-39 is the threshold variant. Trezor/Ledger "
            "warn against storing mnemonics digitally."
        ),
        "references": ["https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki"],
    },
    # ═══════════════════════════════════════════════════════════════════
    # Hashing
    # ═══════════════════════════════════════════════════════════════════
    {
        "rel_key": "crypto:hash:keccak256",
        "title": "Keccak256 — Ethereum's Hash Function",
        "category": "hashing",
        "text": (
            "Keccak256 is the SHA-3 finalist sponge construction used by "
            "Ethereum for addresses (keccak256(pubkey)[-20:]), event "
            "signatures, and storage layout. Important distinction: "
            "Ethereum uses the original Keccak (pre-NIST padding), NOT "
            "standard SHA3-256 (NIST changed the padding from 0x01 to "
            "0x06). keccak256('Transfer(address,address,uint256)') = "
            "0xddf252ad... as an example of event topic hashing. The "
            "sponge absorbs 1088-bit rate blocks and squeezes 256 bits. "
            "Keccak is not vulnerable to length-extension (unlike "
            "SHA-256/MD5), which matters for commitment and MAC design. "
            "In Solidity, keccak256(abi.encodePacked(...)) is a common "
            "source of hash collisions when types are ambiguous — use "
            "abi.encode for type-safe hashing."
        ),
        "references": ["https://ethereum.org/en/developers/docs/evm/"],
    },
    {
        "rel_key": "crypto:hash:sha256",
        "title": "SHA-256",
        "category": "hashing",
        "text": (
            "SHA-256 is a Merkle-Damgård hash from the SHA-2 family, "
            "outputting 256 bits with 64 rounds of the compression function. "
            "Used by Bitcoin (double-SHA256 for blocks/transactions), "
            "Taproot tweaks, and as a generic hashing primitive. SHA-256 "
            "is vulnerable to length-extension attacks, so MACs must use "
            "HMAC-SHA256. It is not 'memory-hard', making it ASIC-"
            "friendly — hence Bitcoin's ASIC mining arms race. Security "
            "margin: preimage 2^256, collision 2^128 (birthday bound). "
            "In zk contexts, SHA-256 circuits are large (~25k constraints) "
            "compared to Pedersen/Poseidon, which is why rollups prefer "
            "ZK-friendly hashes."
        ),
        "references": ["https://en.wikipedia.org/wiki/SHA-2"],
    },
    {
        "rel_key": "crypto:hash:pedersen",
        "title": "Pedersen Hash / Commitments",
        "category": "hashing",
        "text": (
            "Pedersen commitments commit to a value v with randomness r as "
            "C = v*G + r*H, where G and H are independent generators. The "
            "commitment is hiding (r hides v) and binding (cannot find "
            "v'≠v with the same C without breaking DL). Pedersen hashes are "
            "additively homomorphic: C(a) + C(b) = C(a+b), enabling "
            "range proofs and privacy protocols. Zcash's Jubjub "
            "Pedersen hashes and the 'Baby Jubjub' Edwards curve are used "
            "in zk-SNARK circuits. In circuits, Pedersen hashes are far "
            "cheaper than SHA-256. Security caveat: Pedersen commitments "
            "are NOT collision-resistant in the traditional sense — the "
            "binding property is computational, not information-theoretic."
        ),
        "references": ["https://en.wikipedia.org/wiki/Commitment_scheme"],
    },
    {
        "rel_key": "crypto:hash:poseidon",
        "title": "Poseidon — ZK-Friendly Hash",
        "category": "hashing",
        "text": (
            "Poseidon is an arithmetization-oriented (AO) hash built for "
            "efficiency inside zk-SNARK circuits: one Poseidon hash costs "
            "~250 constraints vs ~25k for SHA-256. It is a SPONGE "
            "construction over a prime field with an optimized round "
            "structure (full rounds + partial rounds) and MDS matrix "
            "mixing. Poseidon is used in Tornado Cash v2, Semaphore, "
            "zkSync, and many privacy apps for Merkle tree leaves and "
            "nullifiers. Trade-offs: nonstandard, younger cryptanalysis; "
            "parameter selection matters (the S-box exponent, number of "
            "rounds). Variants: Poseidon2, Rescue, Griffin, and the "
            "Argon2-adjacent 'Reinforced Concrete' (Anemoi). Always use "
            "audited parameters from the reference implementation."
        ),
        "references": ["https://eprint.iacr.org/2019/458"],
    },
    {
        "rel_key": "crypto:hash:mimc",
        "title": "MiMC — Minimal Multiplicative Complexity Hash",
        "category": "hashing",
        "text": (
            "MiMC is an early ZK-friendly block cipher/hash designed to "
            "minimize multiplicative complexity (squaring-based S-box): "
            "each round is x -> (x + k)^3 over a prime field. It was used "
            "in early ZCash circuits (PedersenHash/MiMC for commitment "
            "trees) before being superseded by Poseidon. MiMC is simple "
            "to implement in circuits but has a smaller cryptanalytic "
            "track record than Poseidon; its security relies on the "
            "hardness of discrete log-like operations in finite fields. "
            "Rarely recommended for new designs today."
        ),
        "references": ["https://eprint.iacr.org/2016/492"],
    },
    # ═══════════════════════════════════════════════════════════════════
    # Commitment schemes
    # ═══════════════════════════════════════════════════════════════════
    {
        "rel_key": "crypto:commit:merkle",
        "title": "Merkle Trees",
        "category": "commitment-schemes",
        "text": (
            "A Merkle tree commits to a set of leaves with a single root: "
            "each internal node is the hash of its children, and the root "
            "commits to all leaves. Merkle proofs are O(log n) hashes that "
            "prove membership without revealing other leaves. Used "
            "everywhere in Web3: Ethereum Patricia-Merkle tries, Bitcoin "
            "Merkle roots in blocks, rollup state roots, airdrop claims, "
            "and zk Merkle membership. Properties: any leaf change changes "
            "the root (binding); proof verification is O(log n) hashes. "
            "Variants: sparse Merkle trees (key-value stores, used in "
            "Celestia), sorted Merkle trees (non-membership proofs), and "
            "Merkle-Patricia tries (Ethereum). Security: must use "
            "collision-resistant hashing; standard Merkle trees leak "
            "sibling data in proofs (not hiding)."
        ),
        "references": ["https://en.wikipedia.org/wiki/Merkle_tree"],
    },
    {
        "rel_key": "crypto:commit:verkle",
        "title": "Verkle Trees — Vector Commitments",
        "category": "commitment-schemes",
        "text": (
            "Verkle trees replace Merkle trees' hash-based proofs with "
            "vector commitments (KZG polynomial commitments), shrinking "
            "proof sizes from O(log n) hashes to a constant-size proof per "
            "level. Ethereum's Verkle tree proposal (EIP-6800/6806) uses "
            "Verkle tries with IPA commitments to make stateless clients "
            "feasible. Each node commits to its children with a polynomial "
            "commitment; proving a key-value pair requires an opening "
            "proof. Trade-offs: KZG needs a trusted setup (or IPA "
            "transparent), and computing commitments is more expensive "
            "than hashing. Verkle trees are a key component of "
            "'verkleization' for Ethereum state."
        ),
        "references": ["https://ethereum.org/en/roadmap/verkle-trees/"],
    },
    {
        "rel_key": "crypto:commit:kzg",
        "title": "KZG / Polynomial Commitments",
        "category": "commitment-schemes",
        "text": (
            "KZG (Kate-Zaverucha-Goldberg) polynomial commitments commit to "
            "a polynomial P(x) with one group element, and open evaluations "
            "P(a) = y with a constant-size proof using pairing: commit = "
            "g^(P(τ)), open proof = g^((P(x)-y)/(x-a)) evaluated at the "
            "trapdoor τ. KZG underpins PLONK, Danksharding (EIP-4844 "
            "blobs), and Verkle trees. Requires a trusted setup (powers of "
            "tau) for the trapdoor τ; after the ceremony, τ is discarded. "
            "Key properties: constant-size proofs, homomorphic "
            "commitments, and efficient batch opening. Security rests on "
            "the q-SDH assumption. In EIP-4844, blobs are committed with "
            "KZG and proven at the point z=1 during block verification."
        ),
        "references": [
            "https://dankradfeist.de/ethereum/2020/06/16/kate-polynomial-commitments.html"
        ],
    },
    {
        "rel_key": "crypto:commit:hash-commit-reveal",
        "title": "Hash Commit-Reveal Scheme",
        "category": "commitment-schemes",
        "text": (
            "A hash commit-reveal commits to a value by publishing "
            "C = H(v || r) with random blinding r; later the committer "
            "reveals (v, r) and anyone verifies H(v || r) == C. Properties: "
            "hiding (C reveals nothing about v when r is random) and "
            "binding (cannot find v'≠v with the same C under collision "
            "resistance). Used in on-chain randomness, sealed bids, and "
            "fair launch protocols. Caveats: the commitment must be "
            "verified BEFORE the reveal; blinding is mandatory (a bare "
            "H(v) is vulnerable to brute-force dictionary attacks); and "
            "the protocol must prevent front-running of the reveal phase. "
            "Ethereum's RANDAO uses a commit-reveal for randomness with "
            "withdrawal incentives."
        ),
        "references": ["https://en.wikipedia.org/wiki/Commitment_scheme"],
    },
    # ═══════════════════════════════════════════════════════════════════
    # Post-quantum
    # ═══════════════════════════════════════════════════════════════════
    {
        "rel_key": "crypto:pq:lattice",
        "title": "Lattice-Based Cryptography (Kyber, Dilithium)",
        "category": "post-quantum",
        "text": (
            "Lattice-based cryptography is the leading post-quantum family: "
            "security reduces to hard lattice problems (LWE, SIS, NTRU). "
            "NIST selected ML-KEM (Kyber) for key encapsulation and "
            "ML-DSA (Dilithium) for signatures in FIPS 203/204 (2024). "
            "Kyber uses Module-LWE with small matrices; Dilithium is a "
            "Fiat-Shamir signature from Module-SIS/LWE. They are "
            "efficient on commodity hardware and resist Shor's algorithm "
            "because lattice problems are believed quantum-hard. "
            "Considerations: larger keys/signatures than ECDSA (Kyber "
            "public keys ~800B, Dilithium signatures ~2.4KB), and "
            "side-channel/implementation complexity. Web3 impact: "
            "ECDSA/Secp256k1 keys are NOT quantum-safe; a CRQC "
            "(cryptographically relevant quantum computer) could recover "
            "private keys from public ones — hence the push to "
            "quantum-resistant wallets and ZK-based migration."
        ),
        "references": ["https://csrc.nist.gov/projects/post-quantum-cryptography"],
    },
    {
        "rel_key": "crypto:pq:hash-signatures",
        "title": "Hash-Based Signatures (XMSS, LMS, SPHINCS+)",
        "category": "post-quantum",
        "text": (
            "Hash-based signatures (HBS) rely only on the security of hash "
            "functions, making them quantum-resistant with minimal "
            "assumptions. Stateful HBS: XMSS and LMS (NIST SP 800-208) "
            "use Merkle trees of one-time signatures (WOTS+); they are "
            "efficient and small but STATE-FUL — reusing a state leaks "
            "keys. Stateless HBS: SPHINCS+ (FIPS 205, NIST 2024) avoids "
            "state by combining few-time signatures with a hypertree; "
            "signatures are large (~17KB) but stateless and "
            "conservative. Hash-based schemes are well suited to firmware "
            "signing, code signing, and long-term archives; in Web3 they "
            "are candidates for quantum-safe transaction signing but "
            "signature size and state management remain obstacles. "
            "Winternitz OTS signs each message bit-block with a chain of "
            "hashes."
        ),
        "references": ["https://csrc.nist.gov/publications/detail/sp/800-208/final"],
    },
    {
        "rel_key": "crypto:pq:shor-threat",
        "title": "Shor's Algorithm and the Quantum Threat to Web3",
        "category": "post-quantum",
        "text": (
            "Shor's algorithm (1994) factors integers and computes discrete "
            "logs in polynomial time on a quantum computer, breaking RSA, "
            "ECDSA, Ed25519, and BLS on classical curves. A "
            "cryptographically relevant quantum computer (CRQC) with "
            "~2-4k logical qubits could recover secp256k1 private keys "
            "from public keys — a catastrophic risk for Web3 because "
            "public keys are visible on-chain. The industry is migrating "
            "to: post-quantum signatures (lattice/hash-based), "
            "quantum-resistant ZK proofs, and account abstraction with "
            "rotation. Harassment of the ECDSA key space (already-used "
            "addresses) means funds moved to P2PK-style addresses are at "
            "greatest risk. Timeline estimates vary; NIST finalized FIPS "
            "203-205 in 2024."
        ),
        "references": ["https://en.wikipedia.org/wiki/Shor%27s_algorithm"],
    },
]


__all__ = ["CRYPTOGRAPHY_KNOWLEDGE"]
