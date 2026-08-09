"""
彩色输出工具模块

提供统一的彩色控制台输出功能，用于 auto_runner 等脚本的可视化输出。
"""

# ANSI color codes
class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    DIM = '\033[2m'
    BOLD = '\033[1m'
    RESET = '\033[0m'


def _print_colored(text: str, color: str) -> None:
    """打印带颜色的文本，处理编码错误"""
    try:
        print(f"{color}{text}{Colors.RESET}")
    except UnicodeEncodeError:
        print(text)


def print_success(text: str) -> None:
    _print_colored(text, Colors.GREEN)


def print_error(text: str) -> None:
    _print_colored(text, Colors.RED)


def print_warning(text: str) -> None:
    _print_colored(text, Colors.YELLOW)


def print_info(text: str) -> None:
    _print_colored(text, Colors.BLUE)


def print_dim(text: str) -> None:
    _print_colored(text, Colors.DIM)


def print_header(text: str) -> None:
    _print_colored(text, Colors.BOLD)


def print_separator(char: str = "=", length: int = 80) -> None:
    print(char * length)


def print_banner(text: str) -> None:
    """打印醒目的横幅"""
    print()
    print_separator()
    _print_colored(f"  {text}", Colors.BOLD)
    print_separator()
    print()


def print_step(step: int, total: int, description: str) -> None:
    """打印步骤信息"""
    _print_colored(f"\n{'='*60}", Colors.CYAN)
    _print_colored(f"  Step {step}/{total}: {description}", Colors.CYAN)
    _print_colored(f"{'='*60}", Colors.CYAN)


def print_substep(text: str) -> None:
    """打印子步骤信息"""
    _print_colored(f"  → {text}", Colors.DIM)
