# Compiled-runtime

This example provides the ability to run commands like `make` or `clang++` in a workspace, before timing the resulting executable file. It will automatically loop for at least 5 seconds to reduce system noise. You can easily modify the example by changing the [the config variables](evaluate.py) at the top of the file.

If the program does not return with the exit code of 0, the run will be marked as failed. This can be used as an opportunity to include internal system-checks and test cases. For stricter behavior validation, feel free to add your own modifications to the evaluation methods shown for [singularity](../singularity/README.md)