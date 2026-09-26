# Controlled negative-sampling comparison

All recovered positives are retained. Metrics use all baseline-dev candidates.

| Policy | Rows | Positives | Negatives | Neg/pos | Hard/mined | Collision | Random | S2 neg | S3 neg | India rows | US rows | SHA-256 | Macro F0.5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| current_hybrid | 453,429 | 60,539 | 392,890 | 6.49 | 392,890 | 0 | 0 | 196,544 | 196,346 | 184,452 | 268,977 | `a2e3b7b25fd30d14c7bb44f3a473a866046def583dc0da604396af700c43f8ce` | 0.877791 |
| mixed | 403,808 | 60,539 | 343,269 | 5.67 | 236,487 | 50 | 106,732 | 171,697 | 171,572 | 163,996 | 239,812 | `f5c5db23b35e55f0c9fb466affee205b7faa6b504f2ba1cfc28ba01be0ace3df` | 0.882312 |
| mined | 519,936 | 60,539 | 459,397 | 7.59 | 392,890 | 0 | 66,507 | 229,798 | 229,599 | 211,491 | 308,445 | `a1d6b60028744aa211612268e9b6374c1244893bd9f7e3d591dc829affa21aa2` | 0.882601 |

Selected: **mined**. One mining round only. Training process peak for the combined experiment driver: 1698.2 MiB.
