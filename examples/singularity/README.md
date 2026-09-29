# Singularity

Singularity is a chess move generation library written in C++

### Singularity 3

Project Evo's Singularity run 3, just before the release of v1.7.0, recorded **5.11× baseline throughput** across 118 passing attempts.

| Recorded throughput | Nodes/sec |
| --- | ---: |
| Baseline | 402,999,525 |
| Best candidate (#113) | 2,058,090,553 |

These results were all obtained using this example's code

### Example command
```bash
project-evo run ./singularity/ ./singularity-evaluate.py --objective_file ./objective.txt --config ./config.toml -i 80 --db ./databases/singularity.db
```
This runs 80 iterations starting from the checked out branch in `./singularity/`. For an example of an evaluation function, see [evaluate.py](evaluate.py).

Please note that the example uses the external library `filelock` to ensure only 1 benchmark runs at a time (in an effort to remove system noise)
