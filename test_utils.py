import sys
from unittest.mock import MagicMock

# Mock selenium for environments running standalone self-check
for mod in [
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome.options",
    "selenium.webdriver.common.by",
    "selenium.webdriver.common.keys",
    "selenium.webdriver.support.ui",
    "selenium.webdriver.support",
    "selenium.webdriver.support.expected_conditions",
]:
    sys.modules.setdefault(mod, MagicMock())

import os
from utils import PROFILE_DIR

assert os.path.isabs(PROFILE_DIR), "PROFILE_DIR must be absolute path"
assert os.path.basename(PROFILE_DIR) == "chrome_profile", "PROFILE_DIR folder mismatch"
print("Self-check passed: PROFILE_DIR configured correctly.")
