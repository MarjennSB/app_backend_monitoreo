import asyncio
import time
import platform

def _is_linux() -> bool:
    return platform.system().lower() == "linux"

async def _icmp_ping(ip: str) -> tuple[bool, float]:
    if _is_linux():
        cmd = ["ping", "-c", "1", "-W", "1", ip]
    else:
        cmd = ["ping", "-n", "1", "-w", "1000", ip]

    t_start = time.monotonic_ns()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        elapsed_ns = time.monotonic_ns() - t_start
        is_alive   = proc.returncode == 0
        print(f"Stdout: {stdout.decode('cp850', errors='replace')}")
        print(f"Exit code: {proc.returncode}")
        return is_alive, 0.0
    except Exception as e:
        print(f"Exception: {e}")
        return False, 0.0

if __name__ == "__main__":
    asyncio.run(_icmp_ping("192.168.1.37"))
