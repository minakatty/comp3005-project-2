# Module 1 measurements

## 1. Capacity

| schema | columns | record bytes L | computed floor(4088/(L+4)) | measured per full page | data pages for 3000 rows |
|---|---|---|---|---|---|
| all INT | `a:INT,b:INT,c:INT` | 12 | 255 | 255 | 12 |
| short VARCHAR | `a:INT,b:VARCHAR(5),c:INT` | 13 | 240 | 240 | 13 |
| VARCHAR(200) | `a:INT,s:VARCHAR(200)` | 206 | 19 | 19 | 158 |

## 2. Replacement policies (traces from `traces`)

| trace | policy | B | hits | misses | evictions | hit rate |
|---|---|---|---|---|---|---|
| trace1.txt | lru | 4 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 16 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 64 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | lru | 256 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 4 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 16 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 64 | 2 | 4 | 0 | 0.3333 |
| trace1.txt | clock | 256 | 2 | 4 | 0 | 0.3333 |
| trace2.txt | lru | 4 | 597 | 4403 | 4399 | 0.1194 |
| trace2.txt | lru | 16 | 2284 | 2716 | 2700 | 0.4568 |
| trace2.txt | lru | 64 | 4070 | 930 | 866 | 0.8140 |
| trace2.txt | lru | 256 | 4474 | 526 | 270 | 0.8948 |
| trace2.txt | clock | 4 | 591 | 4409 | 4405 | 0.1182 |
| trace2.txt | clock | 16 | 2175 | 2825 | 2809 | 0.4350 |
| trace2.txt | clock | 64 | 4038 | 962 | 898 | 0.8076 |
| trace2.txt | clock | 256 | 4476 | 524 | 268 | 0.8952 |
| trace3.txt | lru | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | lru | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | lru | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | lru | 256 | 720 | 80 | 0 | 0.9000 |
| trace3.txt | clock | 4 | 0 | 800 | 796 | 0.0000 |
| trace3.txt | clock | 16 | 0 | 800 | 784 | 0.0000 |
| trace3.txt | clock | 64 | 0 | 800 | 736 | 0.0000 |
| trace3.txt | clock | 256 | 720 | 80 | 0 | 0.9000 |
| trace4.txt | lru | 4 | 97 | 4903 | 4899 | 0.0194 |
| trace4.txt | lru | 16 | 397 | 4603 | 4587 | 0.0794 |
| trace4.txt | lru | 64 | 1578 | 3422 | 3358 | 0.3156 |
| trace4.txt | lru | 256 | 4800 | 200 | 0 | 0.9600 |
| trace4.txt | clock | 4 | 98 | 4902 | 4898 | 0.0196 |
| trace4.txt | clock | 16 | 394 | 4606 | 4590 | 0.0788 |
| trace4.txt | clock | 64 | 1560 | 3440 | 3376 | 0.3120 |
| trace4.txt | clock | 256 | 4800 | 200 | 0 | 0.9600 |
| trace5.txt | lru | 4 | 758 | 2242 | 2238 | 0.2527 |
| trace5.txt | lru | 16 | 1900 | 1100 | 1084 | 0.6333 |
| trace5.txt | lru | 64 | 1900 | 1100 | 1036 | 0.6333 |
| trace5.txt | lru | 256 | 1990 | 1010 | 754 | 0.6633 |
| trace5.txt | clock | 4 | 748 | 2252 | 2248 | 0.2493 |
| trace5.txt | clock | 16 | 1900 | 1100 | 1084 | 0.6333 |
| trace5.txt | clock | 64 | 1900 | 1100 | 1036 | 0.6333 |
| trace5.txt | clock | 256 | 1970 | 1030 | 774 | 0.6567 |


## 3. Scan cost

Table Big: 500 data pages + 1 directory page, 120000 rows of 13 bytes.

| B | page reads | hits | misses | evictions | max frames pinned |
|---|---|---|---|---|---|
| 2 | 501 | 0 | 501 | 501 | 1 |
| 8 | 501 | 0 | 501 | 495 | 1 |
| 64 | 501 | 0 | 501 | 439 | 1 |
