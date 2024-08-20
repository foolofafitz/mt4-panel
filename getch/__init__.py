import ctypes
from ctypes.util import find_library
import pathlib

path = pathlib.Path(__file__).absolute().parent / "libgetch.so"
c_lib = ctypes.CDLL(path)

def getch():
    return c_lib.getch()
