from .apolar import ApolarCollector
from .habitec import HabitecCollector
from .innova import InnovaCollector

COLLECTORS = {
    "apolar": ApolarCollector,
    "innova": InnovaCollector,
    "habitec": HabitecCollector,
}
