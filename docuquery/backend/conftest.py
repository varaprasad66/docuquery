"""Select the guarded disposable database before pytest imports application modules."""
import tests_integration  # noqa: F401 - deliberate early harness initialization
