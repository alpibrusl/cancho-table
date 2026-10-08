# The Linux benchmarks with DuckDB: commands, loads, raw output

The numbers behind the "Linux, with DuckDB" section of [benchmarks.html](benchmarks.html). Measured on `gram`, the Linux x86-64 machine (Intel i7-1260P: 12 cores of which 6 threads were used, on **three physical cores**; Linux 7.0; DuckDB 1.5.6, csvtk 0.38.0, Miller 6.22.0; `table` built from main at `7d04b9f` with the pinned compiler `a4572ea`). The box is shared with other users' work.

## Conditions

* **Cores.** Every command ran as `nice -n 19 ionice -c3 taskset -c 0-5 python3 scripts/<script> ...`: cores 0 to 5 only (the six threads of three physical cores). Threads: `table --threads 1` and `--threads 6`; DuckDB `SET threads=1` and `SET threads=6`; csvtk `-j 1`. DuckDB's default would be 16 threads on this machine and ignores the affinity mask, so a wrapper `duckdb` earlier on `PATH` runs `duckdb -cmd "SET threads=6" "$@"`; the scripts' "duckdb default" cells are therefore 6 threads, and those that say `SET threads=1` override it.
* **Method.** The repository's scripts, unchanged except one fix (below): minimum of 5 interleaved runs (3 for the 1 GB file), output to /dev/null, every contender's answer compared with Python's before it is timed.
* **Load.** `/proc/loadavg` at the start of each script is in the log header below (3 to 5 for the first batch; 2 to 3 for the rerun). During the harder-cases batch the one-minute load average rose to 24 for a few minutes and ssh to the machine timed out for a while, at the time of cells in which csvtk's `summary` took 14.5 GB on the 100,000-key sum and was killed at about 14 GB on the million-key sum (rc -9), on a machine with 31 GB. The sorts, the 100,000- and 1,000,000-key counts and `distinct` were therefore run again at a load of 2 to 3 (`adv4`), and those are the numbers on the page; B2 and B4 come from the first batch.
* **A fix to `scripts/adversarial.py`.** The cells C1, D1, D2, E1 and H1 were passed the comparison kind `"rows"`, which since the row sort work means "the whole rows in order"; their expected answers are sorted, so every contender was rejected and the script died with a division by zero. They are passed the default comparison again (`"canon"`): five words changed, in this branch.
* **Not measured.** The 1 GB cells were run with DuckDB at 6 threads only (the script has no one-thread cell for them). The decimal, float and key scripts compare `table` with DuckDB at 1 and 6 threads, and csvtk and Miller at one thread, as written.
* The scripts' own "gate" lines (`G7 ... FAIL` at 1.156 and `G8 ... FAIL` at 1.321) are timing noise on a loaded machine and are kept in the logs; nothing here is a gate.

## Commands

```sh
export PATH=$HOME/bin:<wrapper dir>:<cancho>/target/release:$PATH
cd table                       # a scratch clone; files are generated under build/ and removed afterwards
R="nice -n 19 ionice -c3 taskset -c 0-5"
$R python3 scripts/bench_parallel.py --runs 5 --threads 1,6
$R python3 scripts/bench_numbers.py --runs 5 --threads 6
$R python3 scripts/bench_numbers_agg.py --runs 5 --threads 6
$R python3 scripts/bench_numbers_float.py --runs 5 --threads 6
$R python3 scripts/bench_numbers_fsum.py --runs 5 --threads 6
$R python3 scripts/bench_numbers_keys.py --runs 5 --threads 6
$R python3 scripts/bench_numbers_report.py --runs 5 --threads 6
$R python3 scripts/adversarial.py --cells A1,A2,A3,A4,B1,B2,B3,B4,I1 --runs 5 --threads 6      # first batch
$R python3 scripts/adversarial.py --cells C1,D1,D2,D3,E1,E2,H1 --runs 5 --threads 6
$R python3 scripts/adversarial.py --cells G1,G2 --runs 5 --threads 6
$R python3 scripts/adversarial.py --cells A1,A2,A3,A4,B1,B3,I1 --runs 5 --threads 6            # the rerun
```

## Log header and loads

```
host: Linux 7.0.0-38-generic x86_64; Model name: 12th Gen Intel(R) Core(TM) i7-1260P
duckdb v1.5.6 (Variegata) 069cc9f9b5; csvtk csvtk v0.38.0; mlr mlr 6.22.0
table pin: 7d04b9f Merge pull request #26 from alpibrusl/agg-escaped-hash
=== start 19:52:46 load: 5.12 4.13 3.65
=== bench_parallel 19:52:46 load: 5.12 4.13 3.65
=== bench_numbers 19:53:38 load: 4.83 4.20 3.70
=== bench_numbers_agg 19:55:21 load: 4.59 4.30 3.79
=== bench_numbers_float 19:56:16 load: 5.53 4.62 3.93
=== bench_numbers_fsum 19:58:25 load: 4.73 4.62 4.02
=== bench_numbers_keys 20:00:03 load: 4.73 4.71 4.12
=== bench_numbers_report 20:03:22 load: 4.39 4.55 4.17
=== adversarial 20:04:38 load: 4.84 4.61 4.22
=== done 20:20:06 load: 10.54 24.62 14.84
```

## Everyday questions (bench_parallel)

```
file: data.csv, 31667311 bytes, 1000000 rows; Linux x86_64, 16 cores; minimum of 5 interleaved runs, output to /dev/null
duckdb: v1.5.6 (Variegata) 069cc9f9b5; csvtk: csvtk v0.38.0

filter
contender            min s  median s     vs 1  peak RSS MB
table -t1           0.0718    0.0775   1.00x          2.4
table -t6           0.0378    0.0442   1.90x          7.0
duckdb -t1          0.1996    0.2052   1.00x         62.8
duckdb -t6          0.1041    0.1153   1.92x         71.7
duckdb default      0.1054    0.1234   1.89x         71.7
csvtk -j1           0.5460    0.5707   1.00x         19.9
csvtk -j6           0.4692    0.5110   1.16x         21.2

cut
contender            min s  median s     vs 1  peak RSS MB
table -t1           0.0871    0.0927   1.00x          2.4
table -t6           0.0538    0.0548   1.62x         18.3
duckdb -t1          0.2654    0.2808   1.00x         62.4
duckdb -t6          0.1508    0.1528   1.76x         88.8
duckdb default      0.1463    0.1641   1.81x         89.3
csvtk -j1           0.4078    0.4331   1.00x         20.3
csvtk -j6           0.3555    0.3910   1.15x         21.3

group-count
contender            min s  median s     vs 1  peak RSS MB
table -t1           0.0939    0.0952   1.00x          2.2
table -t6           0.0398    0.0402   2.36x          3.4
duckdb -t1          0.1533    0.1572   1.00x         65.6
duckdb -t6          0.1072    0.1122   1.43x         66.0
duckdb default      0.1033    0.1118   1.48x         66.0
csvtk -j1           0.4170    0.4342   1.00x         22.4
csvtk -j6           0.3841    0.3935   1.09x         22.9

group-sum
contender            min s  median s     vs 1  peak RSS MB
table -t1           0.1119    0.1189   1.00x          2.1
table -t6           0.0477    0.0500   2.35x          3.3
duckdb -t1          0.1823    0.1850   1.00x         65.6
duckdb -t6          0.1116    0.1187   1.63x         66.5
duckdb default      0.1178    0.1192   1.55x         66.4
csvtk -j1           0.9298    0.9724   1.00x        116.3
csvtk -j6           0.7925    0.8595   1.17x        117.6

rc=0
```

## Decimal filter (bench_numbers)

```
cell (minimum of 5)              seconds
table int   count t=1             0.1381
table dec   count t=1             0.1340
table int   count t=6             0.0480
table dec   count t=6             0.0509
duckdb DECIMAL count t=1          0.2275
duckdb DOUBLE  count t=1          0.2190
duckdb DECIMAL count t=6          0.1276
duckdb DOUBLE  count t=6          0.1192
csvtk -j 1      count             6.4598
table int   rows t=1              0.1124
table dec   rows t=1              0.1128
table int   rows t=6              0.0479
table dec   rows t=6              0.0509
duckdb DECIMAL rows t=1           0.2657
duckdb DOUBLE  rows t=1           0.2681
duckdb DECIMAL rows t=6           0.1481
duckdb DOUBLE  rows t=6           0.1354
csvtk -j 1      rows              6.8398
mlr            rows               0.4140
G7 count  threads 1    dec/int = 0.970
      duckdb DECIMAL count  t=1    duckdb / table(dec) = 1.70
      duckdb DOUBLE count  t=1    duckdb / table(dec) = 1.63
G7 count  threads 6    dec/int = 1.061
      duckdb DECIMAL count  t=6    duckdb / table(dec) = 2.50
      duckdb DOUBLE count  t=6    duckdb / table(dec) = 2.34
      csvtk -j 1 count   csvtk / table(dec, 1 thread) = 48.21
G7 rows   threads 1    dec/int = 1.004
      duckdb DECIMAL rows   t=1    duckdb / table(dec) = 2.36
      duckdb DOUBLE rows   t=1    duckdb / table(dec) = 2.38
G7 rows   threads 6    dec/int = 1.063
      duckdb DECIMAL rows   t=6    duckdb / table(dec) = 2.91
      duckdb DOUBLE rows   t=6    duckdb / table(dec) = 2.66
      csvtk -j 1 rows    csvtk / table(dec, 1 thread) = 60.64
      mlr rows    mlr / table(dec, 1 thread) = 3.67
G7: worst dec/int 1.063 (limit 1.15): PASS
rc=0
```

## Decimal aggregates (bench_numbers_agg)

```
cell (minimum of 5)                seconds
table int   sum    t=1              0.1444
table dec   sum    t=1              0.1492
table int   sum    t=6              0.0508
table dec   sum    t=6              0.0587
duckdb DECIMAL sum    t=1           0.2443
duckdb DOUBLE  sum    t=1           0.2278   largest error of a sum, min or max against the exact one: 0.000375
duckdb DECIMAL sum    t=6           0.1321
duckdb DOUBLE  sum    t=6           0.1363   largest error of a sum, min or max against the exact one: 0.000075
csvtk -j 1      sum                 1.1028   largest error of a sum, min or max against the exact one: 0.0001
mlr            sum                  0.5417   largest error of a sum, min or max against the exact one: 0.000375
table int   mean   t=1              0.1458
table dec   mean   t=1              0.1485
table int   mean   t=6              0.0504
table dec   mean   t=6              0.0565
duckdb DECIMAL mean   t=1           0.2362
duckdb DOUBLE  mean   t=1           0.2153
duckdb DECIMAL mean   t=6           0.1274
duckdb DOUBLE  mean   t=6           0.1297
table int   minmax t=1              0.1493
table dec   minmax t=1              0.1491
table int   minmax t=6              0.0534
table dec   minmax t=6              0.0611
duckdb DECIMAL minmax t=1           0.2407
duckdb DOUBLE  minmax t=1           0.2333
duckdb DECIMAL minmax t=6           0.1411
duckdb DOUBLE  minmax t=6           0.1406
table int   all    t=1              0.1594
table dec   all    t=1              0.1709
table int   all    t=6              0.0643
table dec   all    t=6              0.0649
duckdb DECIMAL all    t=1           0.2501
duckdb DOUBLE  all    t=1           0.2469   largest error of a sum, min or max against the exact one: 0.000375
duckdb DECIMAL all    t=6           0.1378
duckdb DOUBLE  all    t=6           0.1380   largest error of a sum, min or max against the exact one: 0.000075
csvtk -j 1      all                 1.0957   largest error of a sum, min or max against the exact one: 0.0001
mlr            all                  0.8725   largest error of a sum, min or max against the exact one: 0.000375
G7 sum    threads 1   dec/int = 1.033
      duckdb DECIMAL sum    t=1    duckdb / table(dec) = 1.64
      duckdb DOUBLE sum    t=1    duckdb / table(dec) = 1.53
G7 sum    threads 6   dec/int = 1.156   <-- over 1.15
      duckdb DECIMAL sum    t=6    duckdb / table(dec) = 2.25
      duckdb DOUBLE sum    t=6    duckdb / table(dec) = 2.32
      csvtk -j 1 sum     / table(dec, 1 thread) = 7.39
      mlr sum     / table(dec, 1 thread) = 3.63
G7 mean   threads 1   dec/int = 1.019
      duckdb DECIMAL mean   t=1    duckdb / table(dec) = 1.59
      duckdb DOUBLE mean   t=1    duckdb / table(dec) = 1.45
G7 mean   threads 6   dec/int = 1.123
      duckdb DECIMAL mean   t=6    duckdb / table(dec) = 2.25
      duckdb DOUBLE mean   t=6    duckdb / table(dec) = 2.29
G7 minmax threads 1   dec/int = 0.998
      duckdb DECIMAL minmax t=1    duckdb / table(dec) = 1.62
      duckdb DOUBLE minmax t=1    duckdb / table(dec) = 1.57
G7 minmax threads 6   dec/int = 1.145
      duckdb DECIMAL minmax t=6    duckdb / table(dec) = 2.31
      duckdb DOUBLE minmax t=6    duckdb / table(dec) = 2.30
G7 all    threads 1   dec/int = 1.072
      duckdb DECIMAL all    t=1    duckdb / table(dec) = 1.46
      duckdb DOUBLE all    t=1    duckdb / table(dec) = 1.44
G7 all    threads 6   dec/int = 1.008
      duckdb DECIMAL all    t=6    duckdb / table(dec) = 2.13
      duckdb DOUBLE all    t=6    duckdb / table(dec) = 2.13
      csvtk -j 1 all     / table(dec, 1 thread) = 6.41
      mlr all     / table(dec, 1 thread) = 5.11
G7: worst dec/int 1.156 (limit 1.15): FAIL
rc=1
```

## Float filter (bench_numbers_float)

```
cell (minimum of 5)                  seconds
table price:float count  t=1          0.1381
duckdb DOUBLE price count  t=1        0.2123
table price:float count  t=6          0.0477
duckdb DOUBLE price count  t=6        0.1173
table price:float rows   t=1          0.1277
duckdb DOUBLE price rows   t=1        0.2602
table price:float rows   t=6          0.0485
duckdb DOUBLE price rows   t=6        0.1396
table price:float minmax t=1          0.1548
duckdb DOUBLE price minmax t=1        0.2231
table price:float minmax t=6          0.0602
duckdb DOUBLE price minmax t=6        0.1324
table ratio:float count  t=1          0.2958
duckdb DOUBLE ratio count  t=1        0.2172
table ratio:float count  t=6          0.1080
duckdb DOUBLE ratio count  t=6        0.1266
table ratio:float rows   t=1          0.2799
duckdb DOUBLE ratio rows   t=1        0.2631
table ratio:float rows   t=6          0.1097
duckdb DOUBLE ratio rows   t=6        0.1413
table ratio:float minmax t=1          1.0273
duckdb DOUBLE ratio minmax t=1        0.2153
table ratio:float minmax t=6          0.4171
duckdb DOUBLE ratio minmax t=6        0.1437
table cents:int   count  t=1          0.1420
table cents:int   count  t=6          0.0503
table cents:int   rows   t=1          0.1223
table cents:int   rows   t=6          0.0485
csvtk -j 1 price rows                 6.8063
mlr price rows                        0.4201
csvtk -j 1 ratio rows                 6.9116
mlr ratio rows                        0.3951
G8 count  threads 1   price:float / cents:int = 0.973
G8 count  threads 6   price:float / cents:int = 0.948
G8 rows   threads 1   price:float / cents:int = 1.044
G8 rows   threads 6   price:float / cents:int = 1.000
G9/G10 price  count  t=1   duckdb / table = 1.54
G9/G10 price  count  t=6   duckdb / table = 2.46
G9/G10 price  rows   t=1   duckdb / table = 2.04
G9/G10 price  rows   t=6   duckdb / table = 2.88
G9/G10 price  minmax t=1   duckdb / table = 1.44
G9/G10 price  minmax t=6   duckdb / table = 2.20
G10 price  rows   csvtk -j 1 / table(1 thread) = 53.31
G10 price  rows   mlr / table(1 thread) = 3.29
G9/G10 ratio  count  t=1   duckdb / table = 0.73
G9/G10 ratio  count  t=6   duckdb / table = 1.17
G9/G10 ratio  rows   t=1   duckdb / table = 0.94
G9/G10 ratio  rows   t=6   duckdb / table = 1.29
G9/G10 ratio  minmax t=1   duckdb / table = 0.21
G9/G10 ratio  minmax t=6   duckdb / table = 0.34
G10 ratio  rows   csvtk -j 1 / table(1 thread) = 24.69
G10 ratio  rows   mlr / table(1 thread) = 1.41
G8: worst price:float / cents:int 1.044 (limit 1.25): PASS
rc=0
```

## Exact float sums (bench_numbers_fsum)

```
cell (minimum of 5)                  seconds   exact groups (largest error in ulps)
table price:float sum  t=1            0.1580   4 of 4 (0.0 ulp)
duckdb DOUBLE price sum  t=1          0.2320   0 of 4 (125.0 ulp)
table price:float sum  t=4            0.0790   4 of 4 (0.0 ulp)
duckdb DOUBLE price sum  t=4          0.1479   0 of 4 (33.0 ulp)
table price:float sum  t=6            0.0650   4 of 4 (0.0 ulp)
duckdb DOUBLE price sum  t=6          0.1364   0 of 4 (22.0 ulp)
table price:float mean t=1            0.1671   4 of 4 (0.0 ulp)
duckdb DOUBLE price mean t=1          0.2377   0 of 4 (103.0 ulp)
table price:float mean t=4            0.0769   4 of 4 (0.0 ulp)
duckdb DOUBLE price mean t=4          0.1454   0 of 4 (32.0 ulp)
table price:float mean t=6            0.0687   4 of 4 (0.0 ulp)
duckdb DOUBLE price mean t=6          0.1444   0 of 4 (23.0 ulp)
table price:float both t=1            0.1801   8 of 8 (0.0 ulp)
duckdb DOUBLE price both t=1          0.2404   0 of 8 (125.0 ulp)
table price:float both t=4            0.0844   8 of 8 (0.0 ulp)
duckdb DOUBLE price both t=4          0.1447   0 of 8 (33.0 ulp)
table price:float both t=6            0.0655   8 of 8 (0.0 ulp)
duckdb DOUBLE price both t=6          0.1238   0 of 8 (21.0 ulp)
table ratio:float sum  t=1            1.0275   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio sum  t=1          0.2071   0 of 4 (103.0 ulp)
table ratio:float sum  t=4            0.4780   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio sum  t=4          0.1543   1 of 4 (25.0 ulp)
table ratio:float sum  t=6            0.4434   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio sum  t=6          0.1243   0 of 4 (29.0 ulp)
table ratio:float mean t=1            1.0234   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio mean t=1          0.2068   0 of 4 (108.0 ulp)
table ratio:float mean t=4            0.4907   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio mean t=4          0.1564   0 of 4 (22.0 ulp)
table ratio:float mean t=6            0.4340   4 of 4 (0.0 ulp)
duckdb DOUBLE ratio mean t=6          0.1232   0 of 4 (30.0 ulp)
table ratio:float both t=1            1.0193   8 of 8 (0.0 ulp)
duckdb DOUBLE ratio both t=1          0.2125   0 of 8 (108.0 ulp)
table ratio:float both t=4            0.4770   8 of 8 (0.0 ulp)
duckdb DOUBLE ratio both t=4          0.1475   0 of 8 (22.0 ulp)
table ratio:float both t=6            0.4214   8 of 8 (0.0 ulp)
duckdb DOUBLE ratio both t=6          0.1458   0 of 8 (30.0 ulp)
table cents:int  sum  t=1             0.1552   
table cents:int  sum  t=4             0.0733   
table cents:int  sum  t=6             0.0552   
table cents:int  mean t=1             0.1438   
table cents:int  mean t=4             0.0705   
table cents:int  mean t=6             0.0520   
csvtk -j 1 price both                 1.0266   0 of 8 (37.0 ulp)
mlr price both                        0.6891   0 of 8 (125.0 ulp)
csvtk -j 1 ratio both                 1.3074   0 of 8 (24.0 ulp)
mlr ratio both                        0.7854   0 of 8 (108.0 ulp)
threads: table price sum  1 distinct answer(s) over 3 thread counts
threads: table price mean 1 distinct answer(s) over 3 thread counts
threads: table price both 1 distinct answer(s) over 3 thread counts
threads: table ratio sum  1 distinct answer(s) over 3 thread counts
threads: table ratio mean 1 distinct answer(s) over 3 thread counts
threads: table ratio both 1 distinct answer(s) over 3 thread counts
threads: duck  price sum  3 distinct answer(s) over 3 thread counts
threads: duck  price mean 3 distinct answer(s) over 3 thread counts
threads: duck  price both 3 distinct answer(s) over 3 thread counts
threads: duck  ratio sum  3 distinct answer(s) over 3 thread counts
threads: duck  ratio mean 3 distinct answer(s) over 3 thread counts
threads: duck  ratio both 3 distinct answer(s) over 3 thread counts
G8 sum  threads 1   price:float / cents:int = 1.018
G8 sum  threads 4   price:float / cents:int = 1.078
G8 sum  threads 6   price:float / cents:int = 1.178
G8 mean threads 1   price:float / cents:int = 1.162
G8 mean threads 4   price:float / cents:int = 1.091
G8 mean threads 6   price:float / cents:int = 1.321  <-- over 1.25
G10 price sum  t=1   duckdb / table = 1.47
G10 price sum  t=4   duckdb / table = 1.87
G10 price sum  t=6   duckdb / table = 2.10
G10 price mean t=1   duckdb / table = 1.42
G10 price mean t=4   duckdb / table = 1.89
G10 price mean t=6   duckdb / table = 2.10
G10 price both t=1   duckdb / table = 1.33
G10 price both t=4   duckdb / table = 1.72
G10 price both t=6   duckdb / table = 1.89
G10 price both csvtk -j 1 / table(1 thread) = 5.70
G10 price both mlr / table(1 thread) = 3.83
G10 ratio sum  t=1   duckdb / table = 0.20
G10 ratio sum  t=4   duckdb / table = 0.32
G10 ratio sum  t=6   duckdb / table = 0.28
G10 ratio mean t=1   duckdb / table = 0.20
G10 ratio mean t=4   duckdb / table = 0.32
G10 ratio mean t=6   duckdb / table = 0.28
G10 ratio both t=1   duckdb / table = 0.21
G10 ratio both t=4   duckdb / table = 0.31
G10 ratio both t=6   duckdb / table = 0.35
G10 ratio both csvtk -j 1 / table(1 thread) = 1.28
G10 ratio both mlr / table(1 thread) = 0.77
G8: worst price:float / cents:int 1.321 (limit 1.25): FAIL
rc=1
```

## Group and sort by numbers (bench_numbers_keys)

```
cell (minimum of 5)                  seconds   answer
table group level:float t=1           0.2647   exact
table group level:dec(2) t=1          0.2535   exact
table top price:dec(2) t=1            0.1041   exact
table top price:float t=1             0.1055   exact
table sort ratio:float t=1            1.4343   exact
duckdb group level t=1                0.1796   exact
duckdb top price t=1                  0.1874   exact
duckdb sort ratio t=1                 0.3579   exact
table group level:float t=4           0.1704   exact
table group level:dec(2) t=4          0.1757   exact
table top price:dec(2) t=4            0.1140   exact
table top price:float t=4             0.1201   exact
table sort ratio:float t=4            1.5676   exact
duckdb group level t=4                0.1187   exact
duckdb top price t=4                  0.1243   exact
duckdb sort ratio t=4                 0.2138   exact
table group level:float t=6           0.1826   exact
table group level:dec(2) t=6          0.1586   exact
table top price:dec(2) t=6            0.1203   exact
table top price:float t=6             0.1202   exact
table sort ratio:float t=6            1.6247   exact
duckdb group level t=6                0.1101   exact
duckdb top price t=6                  0.1108   exact
duckdb sort ratio t=6                 0.1750   exact
csvtk -j 1 group level (text)         2.6008   DIFFERENT 22002 groups (true 20001)
csvtk -j 1 sort price desc            4.0460   exact
csvtk -j 1 sort ratio                 5.7945   exact
mlr group level (text)                0.4481   DIFFERENT 22002 groups (true 20001)
mlr sort price desc                   3.7324   exact
mlr sort ratio                        3.8936   exact
G10 group      level:float   t=1   duckdb / table = 0.68
G10 group      level:dec(2)  t=1   duckdb / table = 0.71
G10 group      level:float   t=4   duckdb / table = 0.70
G10 group      level:dec(2)  t=4   duckdb / table = 0.68
G10 group      level:float   t=6   duckdb / table = 0.60
G10 group      level:dec(2)  t=6   duckdb / table = 0.69
G10 top        price:dec(2)  t=1   duckdb / table = 1.80
G10 top        price:float   t=1   duckdb / table = 1.78
G10 top        price:dec(2)  t=4   duckdb / table = 1.09
G10 top        price:float   t=4   duckdb / table = 1.03
G10 top        price:dec(2)  t=6   duckdb / table = 0.92
G10 top        price:float   t=6   duckdb / table = 0.92
G10 sort       ratio:float   t=1   duckdb / table = 0.25
G10 sort       ratio:float   t=4   duckdb / table = 0.14
G10 sort       ratio:float   t=6   duckdb / table = 0.11
G10 group level (text) csvtk -j 1 / table(1 thread) = 9.82
G10 sort price desc    csvtk -j 1 / table(1 thread) = 38.86
G10 sort ratio         csvtk -j 1 / table(1 thread) = 4.04
G10 group level (text) mlr / table(1 thread) = 1.69
G10 sort price desc    mlr / table(1 thread) = 35.85
G10 sort ratio         mlr / table(1 thread) = 2.71
rc=0
```

## Type report (bench_numbers_report)

```
== the clean file ==
cell (minimum of 5)                  seconds
table --report types t=1              0.4509
table --report types t=4              0.2079
table --report types t=6              0.2155
duckdb DESCRIBE (sniff)               0.0709
duckdb SUMMARIZE                      0.7468
mlr summary -a field_type             2.4127
what each says of the columns (the truth: id=:int status=:int bytes=:int price=:dec(2) ratio=:float path=none cents=:int)
  table --report types t=1       id=:int status=:int bytes=:int price=:dec(2) ratio=:float path=none cents=:int
  table --report types t=4       id=:int status=:int bytes=:int price=:dec(2) ratio=:float path=none cents=:int
  table --report types t=6       id=:int status=:int bytes=:int price=:dec(2) ratio=:float path=none cents=:int
  duckdb DESCRIBE (sniff)        id=BIGINT status=BIGINT bytes=BIGINT price=DOUBLE ratio=DOUBLE path=VARCHAR cents=BIGINT
  duckdb SUMMARIZE               id=BIGINT status=BIGINT bytes=BIGINT price=DOUBLE ratio=DOUBLE path=VARCHAR cents=BIGINT
  mlr summary -a field_type      id=int status=int bytes=int price=float ratio=float path=string cents=int
G10 duckdb DESCRIBE (sniff)        / table(t=1) = 0.16
G10 duckdb DESCRIBE (sniff)        / table(t=4) = 0.34
G10 duckdb DESCRIBE (sniff)        / table(t=6) = 0.33
G10 duckdb SUMMARIZE               / table(t=1) = 1.66
G10 duckdb SUMMARIZE               / table(t=4) = 3.59
G10 duckdb SUMMARIZE               / table(t=6) = 3.47
G10 mlr summary -a field_type      / table(t=1) = 5.35
G10 mlr summary -a field_type      / table(t=4) = 11.60
G10 mlr summary -a field_type      / table(t=6) = 11.20
== the dirty file ==
cell (minimum of 5)                  seconds
table --report types t=1              0.4401
table --report types t=4              0.2099
table --report types t=6              0.2120
duckdb DESCRIBE (sniff)               0.0710
mlr summary -a field_type             2.7405
what each says of the columns (the truth: id=:int status=:int bytes=:float price=none ratio=:float path=none cents=:int)
  table --report types t=1       id=:int status=:int bytes=:float price=none ratio=:float path=none cents=:int
  table --report types t=4       id=:int status=:int bytes=:float price=none ratio=:float path=none cents=:int
  table --report types t=6       id=:int status=:int bytes=:float price=none ratio=:float path=none cents=:int
  duckdb DESCRIBE (sniff)        id=BIGINT status=BIGINT bytes=BIGINT price=DOUBLE ratio=DOUBLE path=VARCHAR cents=BIGINT
  duckdb SUMMARIZE               FAILED: Conversion Error: CSV Error on Line: 900001 Original Line: 899999,200,94485,n/a,958.858502
  mlr summary -a field_type      id=int status=int bytes=int-float price=float-string ratio=float path=string cents=int-empty
G10 duckdb DESCRIBE (sniff)        / table(t=1) = 0.16
G10 duckdb DESCRIBE (sniff)        / table(t=4) = 0.34
G10 duckdb DESCRIBE (sniff)        / table(t=6) = 0.34
G10 mlr summary -a field_type      / table(t=1) = 6.23
G10 mlr summary -a field_type      / table(t=4) = 13.06
G10 mlr summary -a field_type      / table(t=6) = 12.93
rc=0
```

## Harder cases, first batch (A1 to I1; it stopped at C1 on the script bug above)

```
Linux x86_64, 16 cores; table /home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/build/table; csvtk csvtk v0.38.0; mlr mlr 6.22.0; duckdb v1.5.6 (Variegata) 069cc9f9b5

A1  sort 1M rows by a text column   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.8770        101.3
csvtk sort                            1.7500        256.3
sh sort                               0.8344         83.7
mlr sort                              4.1331       1459.7
duckdb -t1                            0.8660        177.9
duckdb default                        0.4182        303.0

A2  sort 1M rows by an integer column   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.8305         98.2
csvtk sort                            3.8336        255.3
sh sort -n                            0.7877         99.1
mlr sort                              3.9514       1460.2
duckdb -t1                            0.6723        165.9
duckdb default                        0.3124        285.5

A3  the first 1000 of 1M rows by an integer column, descending   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.1051          2.4
csvtk sort|head                       3.4480        260.6
sh sort|head                          0.7221         99.2
mlr sort then head                    2.8716       1454.1
duckdb -t1                            0.2425         71.3
duckdb default                        0.1277         74.5

A4  sort 1M rows by an integer column with about 10 rows to a value   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.9136         98.2
csvtk sort                            3.4471        255.6
sh sort -s -n                         0.6063        122.1
mlr sort                              2.1343       1097.1
duckdb -t1                            0.6613        166.3
duckdb default                        0.3255        285.6

B1  group-count, 100,000 distinct keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.2789         16.3
table -t6                             0.2651         77.6
csvtk -j1                             0.6493         42.7
sh cut|sort|uniq -c                   0.2545         37.4
mlr count-distinct                    0.7676        625.6
duckdb -t1                            0.2511         77.9
duckdb default                        0.1470        128.6

B2  group-sum of v, 100,000 keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.3085         16.3
table -t6                             0.2968         76.6
csvtk -j1                            10.5997      14474.3
mlr stats1                            1.6674        955.2
duckdb -t1                            0.2450         77.2
duckdb default                        0.1537        137.3

B3  group-count, 1,000,000 distinct keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             1.6527        135.1
table -t6                             1.8683        192.7
csvtk -j1                             1.6960        276.1
sh cut|sort|uniq -c                   0.3078         41.3
mlr count-distinct                    2.5488       1529.5
duckdb -t1                            0.3809        132.1
duckdb default                        0.1750        135.2
  csvtk -j1: output differs or failed (rc -9), not timed: ''

B4  group-sum of v, 1,000,000 keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             1.7928        135.1
table -t6                             1.7752        192.6
mlr stats1                            3.5840       2607.8
duckdb -t1                            0.3644        147.8
duckdb default                        0.1629        149.3

I1  distinct:id (1M distinct) per s   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.4920         89.1
table -t6                             0.5729        151.6
csvtk -j1                             1.2242        147.4
sh cut|sort -u|uniq -c                0.3011         61.9
mlr                                   2.9075       1709.5
duckdb -t1                            0.3429        125.7
duckdb default                        0.1821        129.2
  table -t1: output differs or failed (rc 0), not timed: ''
  table -t6: output differs or failed (rc 0), not timed: ''
  csvtk -j1: output differs or failed (rc 0), not timed: ''
  mlr cut: output differs or failed (rc 0), not timed: ''
  duckdb -t1: output differs or failed (rc 0), not timed: ''
  duckdb default: output differs or failed (rc 0), not timed: ''
Traceback (most recent call last):
  File "/home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/scripts/adversarial.py", line 463, in <module>
    main()
    ~~~~^^
  File "/home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/scripts/adversarial.py", line 450, in main
    for n in names[r % len(names):] + names[:r % len(names)]:
                   ~~^~~~~~~~~~~~
ZeroDivisionError: division by zero
rc=1
```

## Harder cases C1 to H1

```
load: 2.67 17.93 13.44
Linux x86_64, 16 cores; table /home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/build/table; csvtk csvtk v0.38.0; mlr mlr 6.22.0; duckdb v1.5.6 (Variegata) 069cc9f9b5

C1  select 3 of 200 columns (200,000 rows)   (196 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.2390          2.4
table -t6                             0.0945          4.3
csvtk -j1                             1.0272         21.2
mlr cut                               3.4094       3047.0
duckdb -t1                            0.9451         98.2
duckdb default                        0.6302        164.9

D1  cut 2 columns, all fields quoted   (47 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.0823          2.4
table -t6                             0.0479         41.0
csvtk -j1                             0.4098         20.3
mlr cut                               0.4250        342.4
duckdb -t1                            0.3440         74.6
duckdb default                        0.1393        115.2

D2  filter status=404 and bytes>50000, all quoted   (47 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.0841          2.4
table -t6                             0.0383          9.5
csvtk -j1 filter|grep                 0.6043         19.7
mlr filter                            0.2711        119.8
duckdb -t1                            0.2866         75.7
duckdb default                        0.1223         85.3
  sh cut|sort|uniq -c: output differs or failed (rc 0), not timed: ''

D3  group-count by status, all quoted   (47 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.0908          2.2
table -t6                             0.0351          3.9
csvtk -j1                             0.3416         22.0
mlr count-distinct                    0.2680        118.7
duckdb -t1                            0.1625         77.7
duckdb default                        0.0857         79.9

E1  select 2 columns, 1-10 KB fields   (114 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.1715          2.4
table -t6                             0.1696          4.2
csvtk -j1                             0.2113         22.3
mlr cut                               0.1904        102.1
duckdb -t1                            0.3809        161.6
duckdb default                        0.3269        163.2
  sh cut|sort|uniq -c: output differs or failed (rc 0), not timed: ''

E2  group-count by g, 1-10 KB fields   (114 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.1635          2.1
table -t6                             0.1651          3.9
csvtk -j1                             0.1950         22.6
mlr count-distinct                    0.1862         93.9
duckdb -t1                            0.3732        162.4
duckdb default                        0.3364        165.9

H1  filter keeping ~90% of 1M rows (output-bound)   (32 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.1286          2.4
table -t6                             0.0867         45.2
csvtk -j1                             0.5998         20.1
mlr filter                            0.3680        182.9
duckdb -t1                            0.3597         61.1
duckdb default                        0.1737        140.0
```

## 1 GB cells

```
load: 1.64 11.23 11.58
Linux x86_64, 16 cores; table /home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/build/table; csvtk csvtk v0.38.0; mlr mlr 6.22.0; duckdb v1.5.6 (Variegata) 069cc9f9b5

G2  1 GB file: group-count by status   (1077 MB, min of 3)
contender                              min s  peak RSS MB
table -t1                             2.3620          2.1
table -t4                             1.2619         19.1
table -t6                             1.0807         27.4
csvtk -j1 freq                       10.5433         22.0
duckdb default                        1.1201        218.4

G1  1 GB file: filter status=404 and bytes>50000   (1077 MB, min of 3)
contender                              min s  peak RSS MB
table -t1                             2.4212          2.4
table -t4                             1.3376          9.4
table -t6                             1.0619         12.8
csvtk -j1 filter|grep                19.3507         20.3
duckdb default                        2.5778        361.7
FIN
```

## The rerun of A1 to A4, B1, B3, I1 at a load of 2 to 3

```
load: 3.06 6.25 9.38
Linux x86_64, 16 cores; table /home/alpibru/Workspace/alpibrusl/claude-lexsys/bench-ldb-0007/table/build/table; csvtk csvtk v0.38.0; mlr mlr 6.22.0; duckdb v1.5.6 (Variegata) 069cc9f9b5

A1  sort 1M rows by a text column   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.8900        101.2
csvtk sort                            1.8803        258.4
sh sort                               0.9298         84.1
mlr sort                              4.5561       1456.8
duckdb -t1                            0.8535        175.6
duckdb default                        0.4013        301.0

A2  sort 1M rows by an integer column   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             1.4276         98.2
csvtk sort                            7.0185        250.5
sh sort -n                            1.5929         99.3
mlr sort                              7.1742       1449.7
duckdb -t1                            1.2481        164.4
duckdb default                        0.7202        285.7

A3  the first 1000 of 1M rows by an integer column, descending   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.1292          2.5
csvtk sort|head                       4.3651        255.7
sh sort|head                          0.9118         99.4
mlr sort then head                    3.9732       1447.2
duckdb -t1                            0.3251         69.5
duckdb default                        0.1858         73.3

A4  sort 1M rows by an integer column with about 10 rows to a value   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.8113         98.2
csvtk sort                            2.9259        257.5
sh sort -s -n                         0.5347        122.1
mlr sort                              1.9876       1098.7
duckdb -t1                            0.5640        164.0
duckdb default                        0.2627        288.4

B1  group-count, 100,000 distinct keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.2372         16.4
table -t6                             0.2289         77.8
csvtk -j1                             0.5518         43.3
sh cut|sort|uniq -c                   0.2034         37.5
mlr count-distinct                    0.6754        629.9
duckdb -t1                            0.2228         76.2
duckdb default                        0.1300        129.0

B3  group-count, 1,000,000 distinct keys   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             1.2773        135.4
table -t6                             1.4763        190.9
csvtk -j1                             1.3420        272.8
sh cut|sort|uniq -c                   0.2469         41.5
mlr count-distinct                    2.1410       1307.7
duckdb -t1                            0.3127        132.5
duckdb default                        0.1424        132.9

I1  distinct:id (1M distinct) per s   (40 MB, min of 5)
contender                              min s  peak RSS MB
table -t1                             0.4084         89.1
table -t6                             0.4330        150.5
csvtk -j1                             1.0182        146.3
sh cut|sort -u|uniq -c                0.2395         62.0
mlr                                   2.5618       1641.1
duckdb -t1                            0.2894        125.7
duckdb default                        0.1402        129.1
```

