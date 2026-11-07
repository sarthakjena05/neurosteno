# NeuroSteno multimodal benchmark (SIMULATED) · 2026-11-07T15:40:49

> SIMULATED. Synthetic EEG and synthetic head motion decoded by the real FBCCA and gesture code. Not empirical or clinical evidence.

Command: `python scripts/run_multimodal_benchmark.py --replicates 5 --tasks 14 --idle 180 --test-per-target 40 --test-rest 60 --gesture-trials 40 --gesture-natural 900` · runtime 190.2 s

**Question:** Does adding optional head gestures (IMU) to SSVEP selection increase correctly confirmed messages per minute, and for which synthetic patient profiles?

Primary outcome: correctly confirmed messages per minute (CMPM), mean over replicates with 95% bootstrap CI.
Final confirmation is a decoded EEG YES in every condition; gestures never send.

## Does hybrid help? (verdict = 95% CI of the paired difference excludes 0)

| motion profile | SNR | M4 always-on hybrid vs M3 | M5 optimised vs M3 | best fixed multimodal vs best fixed EEG |
|---|---|---|---|---|
| no_head_control | low | worse (x0.743) | no significant difference (x1.107) | no significant difference (x0.993; hybrid|n2|context|gated vs eeg_only|n2|context|gated) |
| no_head_control | medium | worse (x0.743) | no significant difference (x0.929) | no significant difference (x0.966; motion_assisted|n5|context|gated vs eeg_only|n5|context|gated) |
| no_head_control | high | worse (x0.659) | worse (x0.809) | worse (x0.949; hybrid|n5|context|gated vs eeg_only|n5|context|gated) |
| accurate | low | better (x1.838) | no significant difference (x1.011) | no significant difference (x1.104; hybrid|n2|context|gated vs eeg_only|n2|context|gated) |
| accurate | medium | no significant difference (x1.123) | no significant difference (x0.864) | no significant difference (x1.083; motion_assisted|n5|context|gated vs eeg_only|n5|context|gated) |
| accurate | high | no significant difference (x1.028) | worse (x0.841) | no significant difference (x1.025; hybrid|n5|context|gated vs eeg_only|n5|context|gated) |
| unreliable | low | no significant difference (x1.085) | no significant difference (x1.081) | no significant difference (x0.916; hybrid|n2|context|gated vs eeg_only|n2|context|gated) |
| unreliable | medium | no significant difference (x0.927) | no significant difference (x0.856) | no significant difference (x0.986; motion_assisted|n5|context|gated vs eeg_only|n5|context|gated) |
| unreliable | high | worse (x0.76) | worse (x0.812) | worse (x0.962; hybrid|n5|context|gated vs eeg_only|n5|context|gated) |
| high_motion | low | no significant difference (x1.276) | worse (x0.967) | no significant difference (x0.981; hybrid|n2|context|gated vs eeg_only|n2|context|gated) |
| high_motion | medium | worse (x0.774) | no significant difference (x0.913) | no significant difference (x0.969; hybrid|n2|context|gated vs eeg_only|n2|context|gated) |
| high_motion | high | worse (x0.748) | worse (x0.959) | worse (x0.825; motion_assisted|n2|context|gated vs eeg_only|n2|context|gated) |

## no_head_control · SNR low (1.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n2|context|gated` 0.443 · constrained oracle: `eeg_only|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 0.443 · best fixed multimodal `hybrid|n2|context|gated` 0.440

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 0.752 [0.6377, 0.8973] | 82.592 | 0.0182 | 0.0 | 3.07 | 39.5098 | 481 | 0.551 | -0.308 |
| M2: context-aware EEG-only | 0.753 [0.4822, 1.0552] | 88.514 | 0.0095 | 0.0 | 4.22 | 35.3358 | 481 | 0.552 | -0.309 |
| M3: adaptive EEG-only (NeuroSteno) | 0.243 [0.0842, 0.4604] | 207.738 | 0.0 | 0.0 | 4.52 | 0.0 | 481 | 0.178 | 0.201 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.180 [0.0607, 0.3332] | 219.984 | 0.0 | 0.0 | 3.83 | 0.0 | 609 | 0.119 | 0.263 |
| M5: optimised multimodal policy | 0.269 [0.1802, 0.3667] | 187.674 | 0.0 | 0.0 | 3.3 | 0.0 | 609 | 0.178 | 0.175 |

M5 initial policies: {'eeg_only|n3|context|explicit': 2, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|context|gated': 1, 'eeg_only|n5|static|gated': 1} · policy-selection regret 0.198 [-0.0315, 0.3667] · switches/session 15.2

- M2 vs M1: ΔCMPM +0.001 [-0.2046, 0.233], ratio 1.001, better in 1/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.510 [-0.6562, -0.2789], ratio 0.322, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM -0.062 [-0.1232, -0.0137], ratio 0.743, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM +0.026 [-0.1229, 0.1634], ratio 1.107, better in 4/5 → **no significant difference**
- M5 vs M4: ΔCMPM +0.088 [0.0042, 0.1854], ratio 1.491, better in 4/5 → **better**
- P:hybrid|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM -0.003 [-0.0566, 0.0473], ratio 0.993, better in 1/5 → **no significant difference**

## no_head_control · SNR medium (1.4 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n5|context|gated` 1.251 · constrained oracle: `eeg_only|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 1.251 · best fixed multimodal `motion_assisted|n5|context|gated` 1.209

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.343 [1.2084, 1.4893] | 45.238 | 0.0 | 0.0 | 0.38 | 43.3738 | 481 | 0.984 | -0.092 |
| M2: context-aware EEG-only | 1.670 [1.4508, 1.8988] | 36.8 | 0.0 | 0.0 | 0.24 | 42.5892 | 481 | 1.224 | -0.419 |
| M3: adaptive EEG-only (NeuroSteno) | 0.937 [0.8137, 1.0787] | 65.61 | 0.0 | 0.0 | 0.9 | 0.0 | 481 | 0.686 | 0.315 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.696 [0.6073, 0.8198] | 88.594 | 0.0 | 0.0 | 0.98 | 0.0 | 609 | 0.460 | 0.556 |
| M5: optimised multimodal policy | 0.870 [0.5716, 1.2471] | 81.554 | 0.0 | 0.0 | 1.24 | 0.0 | 609 | 0.576 | 0.382 |

M5 initial policies: {'eeg_only|n3|static|explicit': 1, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|context|gated': 1, 'eeg_only|n5|static|explicit': 2} · policy-selection regret 0.351 [0.0844, 0.6972] · switches/session 3.0

- M2 vs M1: ΔCMPM +0.327 [0.1964, 0.5496], ratio 1.244, better in 5/5 → **better**
- M3 vs M2: ΔCMPM -0.734 [-0.9155, -0.5336], ratio 0.561, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM -0.241 [-0.2667, -0.211], ratio 0.743, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM -0.067 [-0.3317, 0.1756], ratio 0.929, better in 2/5 → **no significant difference**
- M5 vs M4: ΔCMPM +0.174 [-0.0871, 0.4449], ratio 1.25, better in 4/5 → **no significant difference**
- P:motion_assisted|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM -0.042 [-0.1637, 0.0325], ratio 0.966, better in 3/5 → **no significant difference**

## no_head_control · SNR high (2.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n5|context|gated` 2.267 · constrained oracle: `eeg_only|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 2.267 · best fixed multimodal `hybrid|n5|context|gated` 2.152

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.775 [1.5949, 1.9093] | 34.216 | 0.0 | 0.0 | 0.54 | 62.9472 | 481 | 1.301 | 0.492 |
| M2: context-aware EEG-only | 2.046 [1.9645, 2.1278] | 29.394 | 0.0095 | 0.0 | 0.45 | 55.3692 | 481 | 1.499 | 0.222 |
| M3: adaptive EEG-only (NeuroSteno) | 1.607 [1.4198, 1.799] | 38.006 | 0.0 | 0.0 | 0.5 | 0.0 | 481 | 1.178 | 0.660 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 1.059 [0.9225, 1.1963] | 57.982 | 0.0 | 0.0 | 0.41 | 0.0 | 609 | 0.701 | 1.208 |
| M5: optimised multimodal policy | 1.301 [1.0822, 1.6352] | 48.45 | 0.0 | 0.0 | 0.56 | 0.0 | 609 | 0.861 | 0.967 |

M5 initial policies: {'eeg_only|n3|context|explicit': 2, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|static|explicit': 2} · policy-selection regret 0.944 [0.7557, 1.1121] · switches/session 8.2

- M2 vs M1: ΔCMPM +0.271 [0.0835, 0.4557], ratio 1.153, better in 4/5 → **better**
- M3 vs M2: ΔCMPM -0.439 [-0.5927, -0.2416], ratio 0.786, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM -0.548 [-0.6083, -0.4855], ratio 0.659, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM -0.306 [-0.4926, -0.1205], ratio 0.809, better in 0/5 → **worse**
- M5 vs M4: ΔCMPM +0.241 [0.049, 0.4464], ratio 1.228, better in 4/5 → **better**
- P:hybrid|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM -0.116 [-0.1858, -0.0631], ratio 0.949, better in 0/5 → **worse**

## accurate · SNR low (1.0 µV) · 5 replicates

Oracle (hindsight): `hybrid|n2|context|gated` 0.466 · constrained oracle: `hybrid|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 0.422 · best fixed multimodal `hybrid|n2|context|gated` 0.466

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 0.713 [0.6535, 0.79] | 85.21 | 0.0 | 0.0 | 3.15 | 43.0058 | 481 | 0.522 | -0.247 |
| M2: context-aware EEG-only | 0.771 [0.5473, 1.004] | 81.736 | 0.0095 | 0.0 | 3.62 | 34.9518 | 481 | 0.565 | -0.305 |
| M3: adaptive EEG-only (NeuroSteno) | 0.220 [0.0887, 0.3977] | 213.77 | 0.0 | 0.0 | 4.23 | 0.0 | 481 | 0.161 | 0.246 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.404 [0.206, 0.6329] | 162.274 | 0.0 | 1.186 | 1.42 | 0.0 | 609 | 0.267 | 0.062 |
| M5: optimised multimodal policy | 0.222 [0.1292, 0.2942] | 203.408 | 0.0 | 0.0 | 3.54 | 0.0 | 609 | 0.147 | 0.244 |

M5 initial policies: {'eeg_only|n3|context|explicit': 1, 'eeg_only|n5|context|explicit': 1, 'hybrid|n3|context|explicit': 1, 'hybrid|n5|static|explicit': 2} · policy-selection regret 0.214 [-0.0758, 0.4768] · switches/session 20.8

- M2 vs M1: ΔCMPM +0.059 [-0.0921, 0.2172], ratio 1.082, better in 3/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.551 [-0.6847, -0.3759], ratio 0.285, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM +0.184 [0.0981, 0.2462], ratio 1.838, better in 5/5 → **better**
- M5 vs M3: ΔCMPM +0.002 [-0.1267, 0.1007], ratio 1.011, better in 3/5 → **no significant difference**
- M5 vs M4: ΔCMPM -0.182 [-0.3426, -0.0662], ratio 0.55, better in 0/5 → **worse**
- P:hybrid|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM +0.044 [-0.0289, 0.1585], ratio 1.104, better in 3/5 → **no significant difference**

## accurate · SNR medium (1.4 µV) · 5 replicates

Oracle (hindsight): `motion_assisted|n5|context|gated` 1.421 · constrained oracle: `motion_assisted|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 1.312 · best fixed multimodal `motion_assisted|n5|context|gated` 1.421

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.401 [1.2252, 1.5673] | 43.614 | 0.0095 | 0.0 | 0.29 | 47.3724 | 481 | 1.026 | 0.020 |
| M2: context-aware EEG-only | 1.626 [1.3486, 1.9728] | 38.492 | 0.0 | 0.0 | 0.3 | 31.4818 | 481 | 1.192 | -0.205 |
| M3: adaptive EEG-only (NeuroSteno) | 1.006 [0.8805, 1.1588] | 61.002 | 0.0 | 0.0 | 0.8 | 0.0 | 481 | 0.737 | 0.415 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 1.130 [0.8699, 1.4225] | 57.118 | 0.0 | 2.062 | 0.7 | 0.0 | 609 | 0.748 | 0.290 |
| M5: optimised multimodal policy | 0.869 [0.603, 1.1954] | 80.074 | 0.0 | 0.0 | 1.58 | 0.0 | 609 | 0.575 | 0.552 |

M5 initial policies: {'eeg_only|n3|static|explicit': 1, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|static|explicit': 2, 'hybrid|n2|context|explicit': 1} · policy-selection regret 0.655 [0.3386, 0.9708] · switches/session 4.4

- M2 vs M1: ΔCMPM +0.225 [0.0138, 0.559], ratio 1.161, better in 4/5 → **better**
- M3 vs M2: ΔCMPM -0.620 [-0.8531, -0.387], ratio 0.619, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM +0.124 [-0.042, 0.2903], ratio 1.123, better in 4/5 → **no significant difference**
- M5 vs M3: ΔCMPM -0.137 [-0.3781, 0.0541], ratio 0.864, better in 1/5 → **no significant difference**
- M5 vs M4: ΔCMPM -0.261 [-0.6108, -0.0287], ratio 0.769, better in 1/5 → **worse**
- P:motion_assisted|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM +0.109 [-0.0596, 0.2851], ratio 1.083, better in 4/5 → **no significant difference**

## accurate · SNR high (2.0 µV) · 5 replicates

Oracle (hindsight): `hybrid|n5|context|gated` 2.218 · constrained oracle: `hybrid|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 2.164 · best fixed multimodal `hybrid|n5|context|gated` 2.218

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.714 [1.5416, 1.8445] | 35.448 | 0.0 | 0.0 | 0.67 | 51.301 | 481 | 1.256 | 0.503 |
| M2: context-aware EEG-only | 1.996 [1.8802, 2.1147] | 30.204 | 0.0 | 0.0 | 0.54 | 51.2672 | 481 | 1.462 | 0.222 |
| M3: adaptive EEG-only (NeuroSteno) | 1.638 [1.4533, 1.8642] | 37.326 | 0.0 | 0.0 | 0.4 | 0.0 | 481 | 1.200 | 0.580 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 1.684 [1.4779, 1.9206] | 36.416 | 0.0 | 4.864 | 0.22 | 0.0 | 609 | 1.114 | 0.534 |
| M5: optimised multimodal policy | 1.378 [1.1456, 1.7285] | 45.88 | 0.0 | 0.0 | 0.39 | 0.0 | 609 | 0.912 | 0.840 |

M5 initial policies: {'eeg_only|n3|context|explicit': 2, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|static|explicit': 2} · policy-selection regret 0.845 [0.6244, 1.0661] · switches/session 6.4

- M2 vs M1: ΔCMPM +0.281 [0.0368, 0.5744], ratio 1.164, better in 5/5 → **better**
- M3 vs M2: ΔCMPM -0.358 [-0.5117, -0.1972], ratio 0.821, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM +0.046 [-0.0142, 0.0937], ratio 1.028, better in 4/5 → **no significant difference**
- M5 vs M3: ΔCMPM -0.260 [-0.4701, -0.0702], ratio 0.841, better in 1/5 → **worse**
- M5 vs M4: ΔCMPM -0.306 [-0.5337, -0.0789], ratio 0.818, better in 0/5 → **worse**
- P:hybrid|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM +0.054 [-0.007, 0.1222], ratio 1.025, better in 4/5 → **no significant difference**

## unreliable · SNR low (1.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n2|context|gated` 0.441 · constrained oracle: `hybrid|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 0.441 · best fixed multimodal `hybrid|n2|context|gated` 0.404

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 0.676 [0.6519, 0.7049] | 88.936 | 0.0 | 0.0 | 3.73 | 43.2838 | 481 | 0.495 | -0.236 |
| M2: context-aware EEG-only | 0.842 [0.5854, 1.0994] | 76.206 | 0.019 | 0.0 | 3.07 | 43.2152 | 481 | 0.617 | -0.402 |
| M3: adaptive EEG-only (NeuroSteno) | 0.235 [0.0923, 0.4192] | 209.102 | 0.0 | 0.0 | 4.54 | 0.0 | 481 | 0.172 | 0.206 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.255 [0.1049, 0.4626] | 200.282 | 0.0 | 5.49 | 3.49 | 0.0 | 609 | 0.169 | 0.186 |
| M5: optimised multimodal policy | 0.254 [0.1503, 0.4077] | 199.036 | 0.0 | 0.0 | 3.78 | 0.0 | 609 | 0.168 | 0.186 |

M5 initial policies: {'eeg_only|n3|context|explicit': 2, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|context|gated': 1, 'eeg_only|n5|static|gated': 1} · policy-selection regret 0.201 [-0.013, 0.3907] · switches/session 16.6

- M2 vs M1: ΔCMPM +0.166 [-0.0474, 0.3981], ratio 1.246, better in 3/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.608 [-0.8112, -0.4186], ratio 0.279, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM +0.020 [-0.0412, 0.0885], ratio 1.085, better in 3/5 → **no significant difference**
- M5 vs M3: ΔCMPM +0.019 [-0.0235, 0.0663], ratio 1.081, better in 4/5 → **no significant difference**
- M5 vs M4: ΔCMPM -0.001 [-0.0572, 0.0556], ratio 0.997, better in 3/5 → **no significant difference**
- P:hybrid|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM -0.037 [-0.0878, 0.0048], ratio 0.916, better in 2/5 → **no significant difference**

## unreliable · SNR medium (1.4 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n5|context|gated` 1.310 · constrained oracle: `eeg_only|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 1.310 · best fixed multimodal `motion_assisted|n5|context|gated` 1.292

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.406 [1.3227, 1.4894] | 42.882 | 0.0 | 0.0 | 0.27 | 54.729 | 481 | 1.030 | -0.096 |
| M2: context-aware EEG-only | 1.665 [1.4342, 1.9853] | 37.184 | 0.0 | 0.0 | 0.25 | 42.8688 | 481 | 1.220 | -0.355 |
| M3: adaptive EEG-only (NeuroSteno) | 0.911 [0.6918, 1.1269] | 72.232 | 0.0 | 0.0 | 0.73 | 0.0 | 481 | 0.667 | 0.399 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.844 [0.7017, 1.0367] | 74.446 | 0.0 | 5.07 | 0.93 | 0.0 | 609 | 0.559 | 0.466 |
| M5: optimised multimodal policy | 0.779 [0.6745, 0.8624] | 77.27 | 0.0 | 0.0 | 1.82 | 0.0 | 609 | 0.516 | 0.531 |

M5 initial policies: {'eeg_only|n3|static|explicit': 1, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|context|gated': 1, 'eeg_only|n5|static|explicit': 2} · policy-selection regret 0.387 [0.133, 0.7296] · switches/session 4.4

- M2 vs M1: ΔCMPM +0.259 [0.0618, 0.5824], ratio 1.184, better in 5/5 → **better**
- M3 vs M2: ΔCMPM -0.754 [-0.9762, -0.5118], ratio 0.547, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM -0.066 [-0.1933, 0.1141], ratio 0.927, better in 1/5 → **no significant difference**
- M5 vs M3: ΔCMPM -0.132 [-0.3836, 0.1205], ratio 0.856, better in 2/5 → **no significant difference**
- M5 vs M4: ΔCMPM -0.065 [-0.2795, 0.1406], ratio 0.923, better in 2/5 → **no significant difference**
- P:motion_assisted|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM -0.018 [-0.1312, 0.1653], ratio 0.986, better in 1/5 → **no significant difference**

## unreliable · SNR high (2.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n5|context|gated` 2.147 · constrained oracle: `eeg_only|n5|context|gated` · best fixed EEG-only `eeg_only|n5|context|gated` 2.147 · best fixed multimodal `hybrid|n5|context|gated` 2.066

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.750 [1.6137, 1.8697] | 34.54 | 0.0 | 0.0 | 0.53 | 50.9244 | 481 | 1.282 | 0.398 |
| M2: context-aware EEG-only | 2.032 [1.8096, 2.295] | 30.066 | 0.0 | 0.0 | 0.49 | 51.4672 | 481 | 1.489 | 0.115 |
| M3: adaptive EEG-only (NeuroSteno) | 1.601 [1.3827, 1.8345] | 38.488 | 0.0 | 0.0 | 0.45 | 0.0 | 481 | 1.173 | 0.546 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 1.217 [0.9966, 1.4589] | 51.6 | 0.0 | 10.642 | 0.63 | 0.0 | 609 | 0.805 | 0.930 |
| M5: optimised multimodal policy | 1.300 [1.1085, 1.5896] | 48.078 | 0.0 | 0.0 | 0.48 | 0.0 | 609 | 0.860 | 0.847 |

M5 initial policies: {'eeg_only|n3|context|explicit': 2, 'eeg_only|n5|context|explicit': 1, 'eeg_only|n5|static|explicit': 2} · policy-selection regret 0.845 [0.6467, 1.0422] · switches/session 6.2

- M2 vs M1: ΔCMPM +0.283 [-0.0059, 0.7073], ratio 1.162, better in 4/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.431 [-0.5008, -0.3653], ratio 0.788, better in 0/5 → **worse**
- M4 vs M3: ΔCMPM -0.384 [-0.4827, -0.3087], ratio 0.76, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM -0.300 [-0.4862, -0.1152], ratio 0.812, better in 0/5 → **worse**
- M5 vs M4: ΔCMPM +0.083 [-0.085, 0.2482], ratio 1.068, better in 3/5 → **no significant difference**
- P:hybrid|n5|context|gated vs P:eeg_only|n5|context|gated: ΔCMPM -0.081 [-0.1458, -0.022], ratio 0.962, better in 1/5 → **worse**

## high_motion · SNR low (1.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n2|context|gated` 0.237 · constrained oracle: `eeg_only|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 0.237 · best fixed multimodal `hybrid|n2|context|gated` 0.233

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 0.086 [0.0321, 0.1491] | 247.838 | 0.4481 | 0.0 | 19.77 | 27.4892 | 481 | 0.063 | 0.151 |
| M2: context-aware EEG-only | 0.138 [0.0496, 0.2634] | 220.924 | 0.4486 | 0.0 | 16.49 | 31.367 | 481 | 0.102 | 0.099 |
| M3: adaptive EEG-only (NeuroSteno) | 0.139 [0.011, 0.339] | 248.118 | 0.0 | 0.0 | 5.12 | 0.0 | 481 | 0.102 | 0.098 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.177 [0.0061, 0.454] | 242.884 | 0.0 | 9.218 | 4.97 | 0.0 | 609 | 0.117 | 0.060 |
| M5: optimised multimodal policy | 0.134 [0.0103, 0.3278] | 253.568 | 0.0 | 0.284 | 3.84 | 0.0 | 609 | 0.089 | 0.103 |

M5 initial policies: {'eeg_only|n2|context|explicit': 2, 'eeg_only|n2|static|explicit': 1, 'eeg_only|n2|static|gated': 1, 'eeg_only|n3|context|explicit': 1} · policy-selection regret 0.092 [0.0134, 0.17] · switches/session 24.2

- M2 vs M1: ΔCMPM +0.052 [-0.0063, 0.1274], ratio 1.611, better in 4/5 → **no significant difference**
- M3 vs M2: ΔCMPM +0.000 [-0.0776, 0.0867], ratio 1.002, better in 2/5 → **no significant difference**
- M4 vs M3: ΔCMPM +0.038 [-0.0099, 0.11], ratio 1.276, better in 2/5 → **no significant difference**
- M5 vs M3: ΔCMPM -0.005 [-0.0119, -0.0001], ratio 0.967, better in 0/5 → **worse**
- M5 vs M4: ΔCMPM -0.043 [-0.122, 0.0084], ratio 0.758, better in 1/5 → **no significant difference**
- P:hybrid|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM -0.004 [-0.03, 0.0117], ratio 0.981, better in 3/5 → **no significant difference**

## high_motion · SNR medium (1.4 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n2|context|gated` 0.537 · constrained oracle: `eeg_only|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 0.537 · best fixed multimodal `hybrid|n2|context|gated` 0.521

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 0.450 [0.2616, 0.6049] | 133.672 | 0.1 | 0.0 | 6.73 | 31.511 | 481 | 0.330 | 0.087 |
| M2: context-aware EEG-only | 0.500 [0.3075, 0.7709] | 119.662 | 0.0973 | 0.0 | 6.18 | 51.3878 | 481 | 0.366 | 0.037 |
| M3: adaptive EEG-only (NeuroSteno) | 0.462 [0.263, 0.6312] | 144.074 | 0.0 | 0.0 | 2.32 | 0.0 | 481 | 0.339 | 0.075 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.357 [0.1873, 0.5278] | 170.378 | 0.0 | 14.31 | 2.9 | 0.0 | 609 | 0.237 | 0.180 |
| M5: optimised multimodal policy | 0.422 [0.2506, 0.5682] | 149.206 | 0.0 | 0.0 | 2.97 | 0.0 | 609 | 0.279 | 0.116 |

M5 initial policies: {'eeg_only|n2|context|explicit': 2, 'eeg_only|n2|static|explicit': 2, 'hybrid|n3|context|explicit': 1} · policy-selection regret 0.178 [-0.0362, 0.4845] · switches/session 6.2

- M2 vs M1: ΔCMPM +0.050 [-0.1493, 0.2912], ratio 1.112, better in 2/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.038 [-0.2469, 0.1792], ratio 0.924, better in 2/5 → **no significant difference**
- M4 vs M3: ΔCMPM -0.104 [-0.1366, -0.0703], ratio 0.774, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM -0.040 [-0.1563, 0.0388], ratio 0.913, better in 2/5 → **no significant difference**
- M5 vs M4: ΔCMPM +0.064 [-0.0448, 0.1651], ratio 1.18, better in 4/5 → **no significant difference**
- P:hybrid|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM -0.017 [-0.1188, 0.0768], ratio 0.969, better in 3/5 → **no significant difference**

## high_motion · SNR high (2.0 µV) · 5 replicates

Oracle (hindsight): `eeg_only|n2|context|gated` 1.041 · constrained oracle: `eeg_only|n2|context|gated` · best fixed EEG-only `eeg_only|n2|context|gated` 1.041 · best fixed multimodal `motion_assisted|n2|context|gated` 0.859

| condition | CMPM [95% CI] | task s | error | accidental/h | corrections/msg | idle false sent/h | calib s | CMPM 30-min incl. calib | regret |
|---|---|---|---|---|---|---|---|---|---|
| M1: fixed-menu EEG-only | 1.024 [0.9333, 1.1295] | 59.288 | 0.0095 | 0.0 | 0.89 | 31.4018 | 481 | 0.750 | 0.018 |
| M2: context-aware EEG-only | 1.150 [0.9079, 1.4466] | 53.638 | 0.0 | 0.0 | 1.47 | 99.0356 | 481 | 0.843 | -0.108 |
| M3: adaptive EEG-only (NeuroSteno) | 0.845 [0.7368, 0.9806] | 72.772 | 0.0 | 0.0 | 0.57 | 0.0 | 481 | 0.619 | 0.197 |
| M4: hybrid EEG selection + IMU confirmation (always on) | 0.632 [0.4778, 0.8486] | 104.436 | 0.0 | 19.432 | 1.31 | 0.0 | 609 | 0.418 | 0.409 |
| M5: optimised multimodal policy | 0.810 [0.6985, 0.9671] | 76.47 | 0.0 | 0.568 | 0.69 | 0.0 | 609 | 0.536 | 0.231 |

M5 initial policies: {'eeg_only|n2|context|explicit': 1, 'eeg_only|n2|static|explicit': 2, 'eeg_only|n3|context|explicit': 2} · policy-selection regret 0.332 [0.1118, 0.5999] · switches/session 1.8

- M2 vs M1: ΔCMPM +0.126 [-0.06, 0.4107], ratio 1.123, better in 3/5 → **no significant difference**
- M3 vs M2: ΔCMPM -0.305 [-0.5213, -0.0895], ratio 0.734, better in 1/5 → **worse**
- M4 vs M3: ΔCMPM -0.213 [-0.2938, -0.1179], ratio 0.748, better in 0/5 → **worse**
- M5 vs M3: ΔCMPM -0.034 [-0.0836, -0.0021], ratio 0.959, better in 1/5 → **worse**
- M5 vs M4: ΔCMPM +0.178 [0.1035, 0.263], ratio 1.282, better in 5/5 → **better**
- P:motion_assisted|n2|context|gated vs P:eeg_only|n2|context|gated: ΔCMPM -0.183 [-0.294, -0.0705], ratio 0.825, better in 1/5 → **worse**

## Does the policy model predict the simulator?

Across 208 (policy, cell) pairs: Spearman rank correlation 0.882, median measured/predicted CMPM 1.094. Policy-model prediction from CALIBRATION data only vs CMPM measured on held-out simulated sessions for the same fixed policy. Checks the optimiser's model against the simulator, not against patients.

## Gesture detector on held-out synthetic data

- **no_head_control**: held-out sensitivity nod 0.00, shake 0.00, tilt 0.00 · false activations/h nod 0.0, shake 0.0, tilt 0.0 · calibration marked usable (nod/shake/tilt) in nod 0/5, shake 0/5, tilt 0/5
- **accurate**: held-out sensitivity nod 0.97, shake 0.93, tilt 0.88 · false activations/h nod 18.4, shake 0.8, tilt 0.0 · calibration marked usable (nod/shake/tilt) in nod 5/5, shake 5/5, tilt 5/5
- **unreliable**: held-out sensitivity nod 0.45, shake 0.39, tilt 0.28 · false activations/h nod 22.4, shake 10.4, tilt 0.8 · calibration marked usable (nod/shake/tilt) in nod 0/5, shake 1/5, tilt 0/5
- **high_motion**: held-out sensitivity nod 0.29, shake 0.83, tilt 0.49 · false activations/h nod 35.2, shake 23.2, tilt 3.2 · calibration marked usable (nod/shake/tilt) in nod 1/5, shake 5/5, tilt 2/5

## Limitations

- SIMULATION ONLY. EEG and head motion are synthetic; gesture amplitudes, timings, failure rates and incidental movement are modelling assumptions (acquisition/motion_sim.py), not patient data. Only the stationary IMU noise floor comes from a real (not worn) Unicorn recording.
- The simulated patient always intends the right answer; all errors come from the decoders (real FBCCA and real gesture-detector code on synthetic signals, held-out trials).
- Gesture outcomes are drawn from a finite held-out pool per profile and replicate; false activations are Poisson at the rate measured on 15 min of held-out synthetic natural movement.
- Head-motion EEG artifacts are modelled only for the high_motion profile.
- M4 runs hybrid even where the calibration marks gestures unusable (the engine would refuse). It shows what ASSUMING hybrid is better costs; it is not a recommended configuration.
- The oracle is chosen in hindsight on the same test sessions; regret against it is a best-case bound, not an achievable target.
- Calibration overhead is protocol time only (EEG 2/3/5-target calibrations; gesture protocol), amortised over an assumed 30-minute session.
- The policy model (policy.py) is evaluated here against Monte-Carlo simulation of the same simulator; agreement says nothing about real patients.
- No clinical validation. No real patient data. Real-EEG / real-IMU capability estimates require the headset.
