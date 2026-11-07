# NeuroSteno benchmark (SIMULATED) · 2026-11-07T14:44:52

> SIMULATION ONLY. Decoder outcomes come from the real FBCCA code run on SYNTHETIC EEG; the patient is a perfect-intent simulated agent; profiles are synthetic. These are model estimates for comparing strategies, not empirical or clinical results.

Command: `python scripts/run_benchmark.py --replicates 5 --tasks 14 --idle 180 --test-per-target 40 --test-rest 60` · runtime 22.0 s

**Question:** Can context-aware, risk-aware, signal-adaptive navigation increase correctly confirmed medical messages per minute compared with conventional fixed-menu EEG communication?

Primary outcome: **CMPM** = correctly confirmed messages per minute (mean, 95% bootstrap CI over sessions).

## Scenario `low`, task mix `matched` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.258 | [0.1786, 0.3418] | 193.58 | 171.1 | 0.0278 | 11.389 | 27.44 | 62.2 | 2:15 |
| A3 | 0.509 | [0.4632, 0.5525] | 20.89 | 120.8 | 0.0 | 10.869 | 5.28 | 98.9 | 3:15 |
| A5 | 0.710 | [0.6539, 0.7705] | 14.02 | 86.4 | 0.0066 | 9.237 | 50.98 | 69.6 | 5:15 |
| B2 | 0.372 | [0.2488, 0.5041] | 73.45 | 142.9 | 0.0099 | 11.163 | 39.48 | 42.9 | 2:15 |
| B3 | 0.292 | [0.2416, 0.3397] | 42.54 | 148.4 | 0.0049 | 10.417 | 13.16 | 46.0 | 3:15 |
| B5 | 0.928 | [0.8278, 1.03] | 10.7 | 67.9 | 0.0164 | 9.07 | 44.37 | 52.2 | 5:15 |
| C | 0.805 | [0.642, 0.9689] | 15.56 | 83.2 | 0.0174 | 9.485 | 36.71 | 49.9 | 3:3,5:12 |
| D | 0.232 | [0.1585, 0.3199] | 10.19 | 200.2 | 0.0 | 0.086 | 0.0 | 132.3 | 3:6,5:9 |
| D+ | 0.422 | [0.3138, 0.5338] | 10.95 | 145.8 | 0.0039 | 0.799 | 1.29 | 95.9 | 2:3,3:3,5:9 |

- B5 vs A5: ΔCMPM +0.219 [0.1413, 0.3113], ratio 1.313 [1.2002, 1.4439], better in 93% of 15 paired sessions
- C vs A5: ΔCMPM +0.096 [-0.0504, 0.2365], ratio 1.129 [0.9116, 1.3358], better in 73% of 15 paired sessions
- D vs A5: ΔCMPM -0.477 [-0.5597, -0.3941], ratio 0.321 [0.2198, 0.4347], better in 0% of 15 paired sessions
- D+ vs A5: ΔCMPM -0.287 [-0.4001, -0.1661], ratio 0.604 [0.46, 0.7592], better in 13% of 15 paired sessions
- D vs C: ΔCMPM -0.573 [-0.7128, -0.4343], ratio 0.324 [0.2169, 0.4434], better in 0% of 15 paired sessions

## Scenario `low`, task mix `uniform` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.257 | [0.1688, 0.3533] | 212.72 | 176.0 | 0.0171 | 11.374 | 30.13 | 62.8 | 2:15 |
| A3 | 0.475 | [0.4377, 0.5135] | 22.37 | 127.5 | 0.0101 | 10.738 | 11.86 | 91.3 | 3:15 |
| A5 | 0.685 | [0.6325, 0.7366] | 14.6 | 88.9 | 0.01 | 9.107 | 50.98 | 67.0 | 5:15 |
| B2 | 0.352 | [0.2533, 0.4553] | 60.04 | 144.2 | 0.0143 | 11.077 | 31.46 | 41.2 | 2:15 |
| B3 | 0.256 | [0.2243, 0.2868] | 44.5 | 162.9 | 0.0049 | 10.388 | 17.09 | 43.3 | 3:15 |
| B5 | 0.810 | [0.7456, 0.8708] | 12.09 | 75.9 | 0.0066 | 9.539 | 36.84 | 48.2 | 5:15 |
| C | 0.698 | [0.5742, 0.8122] | 17.47 | 92.8 | 0.007 | 9.775 | 30.24 | 49.2 | 3:3,5:12 |
| D | 0.156 | [0.0843, 0.2417] | 18.54 | 235.0 | 0.0 | 0.086 | 0.0 | 128.5 | 3:6,5:9 |
| D+ | 0.306 | [0.2121, 0.4037] | 17.12 | 180.3 | 0.0 | 0.637 | 1.27 | 103.2 | 2:3,3:3,5:9 |

- B5 vs A5: ΔCMPM +0.125 [0.0589, 0.1891], ratio 1.201 [1.0924, 1.3025], better in 87% of 15 paired sessions
- C vs A5: ΔCMPM +0.013 [-0.1081, 0.122], ratio 1.028 [0.8461, 1.2115], better in 67% of 15 paired sessions
- D vs A5: ΔCMPM -0.529 [-0.6014, -0.4478], ratio 0.22 [0.1262, 0.3268], better in 0% of 15 paired sessions
- D+ vs A5: ΔCMPM -0.379 [-0.4953, -0.2655], ratio 0.465 [0.32, 0.6092], better in 0% of 15 paired sessions
- D vs C: ΔCMPM -0.542 [-0.6573, -0.4153], ratio 0.241 [0.1473, 0.3389], better in 0% of 15 paired sessions

## Scenario `medium`, task mix `matched` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.290 | [0.1698, 0.4321] | 218.75 | 168.3 | 0.1414 | 14.68 | 30.24 | 50.7 | 2:15 |
| A3 | 0.825 | [0.7623, 0.8941] | 14.11 | 74.5 | 0.0066 | 12.19 | 9.19 | 58.0 | 3:15 |
| A5 | 1.430 | [1.3669, 1.4907] | 6.39 | 42.3 | 0.0 | 9.385 | 43.22 | 34.6 | 5:15 |
| B2 | 0.378 | [0.2547, 0.5106] | 112.82 | 140.4 | 0.0943 | 14.427 | 42.1 | 49.3 | 2:15 |
| B3 | 0.671 | [0.5445, 0.8279] | 20.29 | 86.3 | 0.0 | 11.814 | 15.75 | 33.0 | 3:15 |
| B5 | 1.760 | [1.6094, 1.9145] | 5.1 | 35.1 | 0.0 | 9.402 | 44.82 | 22.9 | 5:15 |
| C | 1.760 | [1.6182, 1.9083] | 5.1 | 35.1 | 0.0 | 9.402 | 44.82 | 22.9 | 5:15 |
| D | 1.020 | [0.9234, 1.1107] | 4.64 | 61.1 | 0.0 | 0.021 | 0.0 | 40.0 | 5:15 |
| D+ | 1.208 | [1.0676, 1.3457] | 7.9 | 52.2 | 0.0 | 0.94 | 0.0 | 30.6 | 5:15 |

- B5 vs A5: ΔCMPM +0.330 [0.201, 0.4729], ratio 1.233 [1.1396, 1.334], better in 93% of 15 paired sessions
- C vs A5: ΔCMPM +0.330 [0.2045, 0.4748], ratio 1.233 [1.1409, 1.3334], better in 93% of 15 paired sessions
- D vs A5: ΔCMPM -0.409 [-0.5257, -0.2943], ratio 0.72 [0.6433, 0.7971], better in 0% of 15 paired sessions
- D+ vs A5: ΔCMPM -0.222 [-0.3667, -0.0617], ratio 0.852 [0.7442, 0.9646], better in 27% of 15 paired sessions
- D vs C: ΔCMPM -0.740 [-0.8537, -0.6219], ratio 0.585 [0.5377, 0.631], better in 0% of 15 paired sessions

## Scenario `medium`, task mix `uniform` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.281 | [0.1898, 0.3791] | 257.58 | 163.8 | 0.1269 | 14.612 | 38.12 | 46.9 | 2:15 |
| A3 | 0.778 | [0.7375, 0.8216] | 14.81 | 78.0 | 0.0099 | 12.243 | 6.58 | 59.8 | 3:15 |
| A5 | 1.358 | [1.3043, 1.4107] | 6.76 | 44.5 | 0.0 | 9.373 | 42.05 | 35.7 | 5:15 |
| B2 | 0.377 | [0.2511, 0.5134] | 151.05 | 143.6 | 0.1019 | 14.311 | 44.57 | 31.8 | 2:15 |
| B3 | 0.502 | [0.4188, 0.5848] | 26.85 | 106.4 | 0.004 | 11.783 | 15.74 | 31.3 | 3:15 |
| B5 | 1.450 | [1.4084, 1.4969] | 6.27 | 41.5 | 0.0033 | 9.448 | 41.89 | 22.9 | 5:15 |
| C | 1.450 | [1.4065, 1.4958] | 6.27 | 41.5 | 0.0033 | 9.448 | 41.89 | 22.9 | 5:15 |
| D | 0.808 | [0.7627, 0.8508] | 5.64 | 75.2 | 0.0 | 0.021 | 0.0 | 45.2 | 5:15 |
| D+ | 0.945 | [0.8667, 1.0239] | 10.36 | 64.8 | 0.0 | 0.86 | 0.0 | 32.7 | 5:15 |

- B5 vs A5: ΔCMPM +0.092 [0.067, 0.1195], ratio 1.07 [1.0492, 1.0934], better in 93% of 15 paired sessions
- C vs A5: ΔCMPM +0.092 [0.0656, 0.1196], ratio 1.07 [1.0486, 1.0917], better in 93% of 15 paired sessions
- D vs A5: ΔCMPM -0.549 [-0.6274, -0.468], ratio 0.601 [0.5544, 0.6469], better in 0% of 15 paired sessions
- D+ vs A5: ΔCMPM -0.413 [-0.5232, -0.3119], ratio 0.702 [0.6308, 0.7716], better in 0% of 15 paired sessions
- D vs C: ΔCMPM -0.642 [-0.7152, -0.5715], ratio 0.56 [0.5213, 0.5972], better in 0% of 15 paired sessions

## Scenario `high`, task mix `matched` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.516 | [0.3564, 0.6916] | 52.85 | 114.3 | 0.0088 | 15.018 | 30.39 | 42.9 | 2:15 |
| A3 | 1.154 | [1.0779, 1.2296] | 12.03 | 52.9 | 0.0 | 14.7 | 15.84 | 42.8 | 3:15 |
| A5 | 1.724 | [1.6013, 1.8529] | 7.15 | 35.6 | 0.0 | 12.207 | 59.28 | 29.1 | 5:15 |
| B2 | 0.773 | [0.6, 0.9569] | 29.05 | 79.9 | 0.0 | 14.855 | 35.55 | 28.1 | 2:15 |
| B3 | 1.150 | [0.9425, 1.3671] | 14.26 | 55.4 | 0.0 | 14.247 | 27.66 | 23.0 | 3:15 |
| B5 | 2.085 | [1.9072, 2.2588] | 5.73 | 29.6 | 0.0033 | 12.531 | 55.2 | 19.8 | 5:15 |
| C | 2.085 | [1.9111, 2.2575] | 5.73 | 29.6 | 0.0033 | 12.531 | 55.2 | 19.8 | 5:15 |
| D | 1.664 | [1.5458, 1.7796] | 4.54 | 36.8 | 0.0 | 0.065 | 0.0 | 24.1 | 5:15 |
| D+ | 2.180 | [2.0306, 2.3376] | 4.92 | 28.1 | 0.0 | 0.789 | 0.0 | 19.0 | 5:15 |

- B5 vs A5: ΔCMPM +0.362 [0.2306, 0.4953], ratio 1.218 [1.1361, 1.3012], better in 87% of 15 paired sessions
- C vs A5: ΔCMPM +0.362 [0.2234, 0.492], ratio 1.218 [1.1321, 1.3061], better in 87% of 15 paired sessions
- D vs A5: ΔCMPM -0.059 [-0.1919, 0.0862], ratio 0.979 [0.8973, 1.0696], better in 47% of 15 paired sessions
- D+ vs A5: ΔCMPM +0.457 [0.32, 0.6123], ratio 1.28 [1.1856, 1.3847], better in 100% of 15 paired sessions
- D vs C: ΔCMPM -0.421 [-0.5296, -0.3142], ratio 0.805 [0.7669, 0.8483], better in 0% of 15 paired sessions

## Scenario `high`, task mix `uniform` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.571 | [0.4231, 0.7352] | 37.21 | 102.1 | 0.0 | 15.275 | 30.29 | 41.5 | 2:15 |
| A3 | 1.073 | [1.0325, 1.1107] | 12.88 | 56.2 | 0.0 | 14.768 | 14.53 | 42.4 | 3:15 |
| A5 | 1.690 | [1.6242, 1.759] | 7.2 | 35.7 | 0.0 | 11.98 | 60.01 | 28.3 | 5:15 |
| B2 | 0.542 | [0.4063, 0.6954] | 36.91 | 102.6 | 0.0 | 14.922 | 30.28 | 26.9 | 2:15 |
| B3 | 0.810 | [0.6319, 0.9946] | 23.59 | 78.2 | 0.0 | 14.07 | 18.38 | 23.3 | 3:15 |
| B5 | 1.839 | [1.7988, 1.8824] | 6.49 | 32.7 | 0.0033 | 12.397 | 51.07 | 18.9 | 5:15 |
| C | 1.839 | [1.7985, 1.8804] | 6.49 | 32.7 | 0.0033 | 12.397 | 51.07 | 18.9 | 5:15 |
| D | 1.398 | [1.3564, 1.4418] | 5.57 | 43.1 | 0.0 | 0.021 | 0.0 | 23.2 | 5:15 |
| D+ | 1.857 | [1.7916, 1.9203] | 5.96 | 32.5 | 0.0 | 0.832 | 0.0 | 19.2 | 5:15 |

- B5 vs A5: ΔCMPM +0.149 [0.1075, 0.1922], ratio 1.092 [1.0649, 1.1213], better in 93% of 15 paired sessions
- C vs A5: ΔCMPM +0.149 [0.1084, 0.1918], ratio 1.092 [1.0638, 1.1215], better in 93% of 15 paired sessions
- D vs A5: ΔCMPM -0.293 [-0.3495, -0.2426], ratio 0.83 [0.8027, 0.8554], better in 0% of 15 paired sessions
- D+ vs A5: ΔCMPM +0.167 [0.134, 0.203], ratio 1.1 [1.0797, 1.1228], better in 100% of 15 paired sessions
- D vs C: ΔCMPM -0.441 [-0.4936, -0.3868], ratio 0.761 [0.7361, 0.7872], better in 0% of 15 paired sessions

## Scenario `degrading`, task mix `matched` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.184 | [0.1359, 0.2405] | 117.45 | 182.9 | 0.3436 | 14.905 | 40.83 | 61.6 | 2:15 |
| A3 | 0.372 | [0.2927, 0.4589] | 49.25 | 136.3 | 0.0375 | 14.839 | 14.49 | 77.3 | 3:15 |
| A5 | 0.448 | [0.3476, 0.5525] | 38.14 | 120.3 | 0.1111 | 11.819 | 70.92 | 58.2 | 5:15 |
| B2 | 0.286 | [0.2255, 0.3473] | 73.73 | 146.8 | 0.2273 | 14.662 | 47.34 | 37.4 | 2:15 |
| B3 | 0.335 | [0.287, 0.3888] | 48.88 | 131.8 | 0.0455 | 14.32 | 26.32 | 43.2 | 3:15 |
| B5 | 0.600 | [0.492, 0.7095] | 25.72 | 97.3 | 0.0724 | 12.003 | 69.4 | 59.2 | 5:15 |
| C | 0.600 | [0.4971, 0.7091] | 25.72 | 97.3 | 0.0724 | 12.003 | 69.4 | 59.2 | 5:15 |
| D | 0.456 | [0.3584, 0.5655] | 6.19 | 120.5 | 0.0 | 0.042 | 0.0 | 78.0 | 5:15 |
| D+ | 0.531 | [0.421, 0.6526] | 8.02 | 109.2 | 0.0 | 0.642 | 0.0 | 73.5 | 5:15 |

- B5 vs A5: ΔCMPM +0.153 [0.0909, 0.2149], ratio 1.438 [1.2666, 1.6281], better in 87% of 15 paired sessions
- C vs A5: ΔCMPM +0.153 [0.0976, 0.2119], ratio 1.438 [1.2619, 1.6242], better in 87% of 15 paired sessions
- D vs A5: ΔCMPM +0.008 [-0.1625, 0.1882], ratio 1.327 [0.8762, 1.8605], better in 53% of 15 paired sessions
- D+ vs A5: ΔCMPM +0.084 [-0.0777, 0.2511], ratio 1.446 [1.0163, 2.0357], better in 73% of 15 paired sessions
- D vs C: ΔCMPM -0.145 [-0.3144, 0.0314], ratio 0.884 [0.6394, 1.163], better in 40% of 15 paired sessions

## Scenario `degrading`, task mix `uniform` (A* = A5, B* = B5)

| Condition | CMPM | 95% CI | sel/correct | task s | error rate | idle false sel/min | idle false sent/h | urgent s | n chosen |
|---|---|---|---|---|---|---|---|---|---|
| A2 | 0.184 | [0.1497, 0.2244] | 101.65 | 179.8 | 0.3261 | 14.986 | 38.35 | 59.4 | 2:15 |
| A3 | 0.401 | [0.3102, 0.4953] | 47.91 | 134.6 | 0.0285 | 14.758 | 15.86 | 74.1 | 3:15 |
| A5 | 0.466 | [0.3911, 0.5437] | 32.56 | 116.7 | 0.1142 | 12.155 | 53.88 | 75.3 | 5:15 |
| B2 | 0.204 | [0.1579, 0.2547] | 99.91 | 172.7 | 0.2857 | 14.73 | 42.27 | 50.5 | 2:15 |
| B3 | 0.270 | [0.2066, 0.3358] | 71.54 | 153.5 | 0.0653 | 14.19 | 34.21 | 36.8 | 3:15 |
| B5 | 0.534 | [0.4264, 0.6479] | 31.07 | 108.9 | 0.1038 | 12.078 | 68.27 | 62.6 | 5:15 |
| C | 0.534 | [0.4201, 0.6454] | 31.07 | 108.9 | 0.1038 | 12.078 | 68.27 | 62.6 | 5:15 |
| D | 0.370 | [0.2989, 0.4512] | 8.02 | 137.4 | 0.0 | 0.128 | 0.0 | 67.5 | 5:15 |
| D+ | 0.461 | [0.3564, 0.5666] | 9.29 | 118.2 | 0.0 | 0.321 | 0.0 | 51.7 | 5:15 |

- B5 vs A5: ΔCMPM +0.069 [0.0075, 0.14], ratio 1.134 [1.0001, 1.2701], better in 80% of 15 paired sessions
- C vs A5: ΔCMPM +0.069 [0.0049, 0.1361], ratio 1.134 [1.0037, 1.2689], better in 80% of 15 paired sessions
- D vs A5: ΔCMPM -0.095 [-0.2279, 0.0515], ratio 0.942 [0.6544, 1.2591], better in 40% of 15 paired sessions
- D+ vs A5: ΔCMPM -0.004 [-0.1443, 0.1247], ratio 1.114 [0.818, 1.4236], better in 60% of 15 paired sessions
- D vs C: ΔCMPM -0.164 [-0.3283, 0.0063], ratio 0.908 [0.5949, 1.2542], better in 40% of 15 paired sessions

## Limitations
- Synthetic EEG: SNR levels are simulator settings, not measurements from people; real SSVEP varies with the person, display, electrode contact and fatigue.
- Perfect-intent patient model: no attention lapses, no changes of mind; only decoder errors occur.
- The fixed-menu baselines A/B/C decode with conventional fixed-window forced choice and confirm only the final message; D uses dynamic stopping, abstention and per-step confirmation. Part of D's difference therefore comes from the decoder protocol, not only navigation; the per-condition rows separate the contributions.
- A* is chosen in hindsight on the test results (oracle), which favours the baseline.
- Tasks in the 'matched' mix come from the same request distribution the context router optimises for; the 'uniform' mix shows the cost when context does not match the patient's need.
- Calibration estimates (used by C and D to pick n) come from 5 trials per target, as in the live protocol, and are optimistic in-sample; test outcomes are held out.
- No real-EEG results are included. Real results must come from REAL-mode sessions and are reported separately.
