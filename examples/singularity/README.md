# Singularity

Singularity is a chess move generation library written in C++

### Example command
```bash
python3 main.py ./singularity/ ./singularity-evaluate.py --objective_file ./objective.txt --config ./config.toml -i 80 --db ./databases/singularity.db
```
This runs 80 iterations starting from the checked out branch in `./singularity/`. For an example of an evaluation function, see [evaluate.py](evaluate.py).

Please note that the example uses the external library `filelock` to ensure only 1 benchmark runs at a time (in an effort to remove system noise)