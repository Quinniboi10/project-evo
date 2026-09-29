class ProjectEvoException(Exception):
    """
    Base class for all project-evo exceptions
    """
    pass

def base_assert(assertion: bool, msg: str, err_type: type[ProjectEvoException]):
    if not assertion:
        raise err_type(msg)

class KillWorkerException(ProjectEvoException):
    """
    Kill the worker and attempt to kill the pool
    """
    pass

class KillPoolException(ProjectEvoException):
    """
    Kill the pool regardless of '--gnhf'
    """
    pass

class ConfigError(KillPoolException):
    """
    Signals misconfiguration
    Type: Kill pool
    """

def assert_config(assertion: bool, msg: str):
    base_assert(assertion, msg, ConfigError)

class EvalError(KillWorkerException):
    """
    Evaluation errored or returned an invalid score
    Type: Kill worker
    """

def assert_eval(assertion: bool, msg: str):
    base_assert(assertion, msg, EvalError)

class DatabaseException(KillPoolException):
    """
    Database encountered fatal error
    Type: Kill pool
    """

def assert_db(assertion: bool, msg: str):
    base_assert(assertion, msg, DatabaseException)