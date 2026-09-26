# Command-runtime

The simplest example, [evaluate.py](evaluate.py) simply runs a command, and times how long it takes. For short commands, it will automatically loop for at least 5 seconds to reduce system noise. To configure for your use case, edit [the config features](evaluate.py) at the top of the file.

If the program does not return with the exit code of 0, the run will be marked as failed. This can be used as an opportunity to include internal system-checks and test cases. For stricter behavior validation, feel free to add your own modifications to the evaluation methods shown for [singularity](../singularity/README.md)