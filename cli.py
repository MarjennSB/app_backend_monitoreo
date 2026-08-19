import os
import sys
import requests
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.text import Text
from rich.panel import Panel

API_URL = "http://localhost:8000/api/v1"
console = Console()
session = requests.Session()

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_banner():
    banner = """
     ██████╗  ██████╗  █████╗ ███╗   ██╗
    ██╔════╝ ██╔════╝ ██╔══██╗████╗  ██║
    ███████╗ ██║      ███████║██╔██╗ ██║
    ╚════██║ ██║      ██╔══██║██║╚██╗██║
    ███████║ ╚██████╗ ██║  ██║██║ ╚████║
    ╚══════╝  ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═══╝
    """
    console.print(Text(banner, style="bold bright_green"))
    console.print(Panel.fit("[bright_black]MVP Monitoreo - SCAN_v1 | Interactive Hacker Terminal[/]", border_style="bright_green"))

def do_login():
    import getpass
    console.print("\n[bold cyan]Authentication Required[/]")
    while True:
        email = Prompt.ask("[bright_green]Email[/]")
        password = Prompt.ask("[bright_green]Password[/]", password=True)
        
        with console.status("[bold green]Authenticating..."):
            try:
                r = session.post(
                    f"{API_URL}/auth/login",
                    data={"username": email, "password": password}
                )
                if r.status_code == 401:
                    console.print("[bold red]❌ Credenciales incorrectas. Intenta de nuevo.[/]")
                    continue
                r.raise_for_status()
                token = r.json()["access_token"]
                session.headers.update({"Authorization": f"Bearer {token}"})
                console.print("[bold green]✅ Login exitoso[/]\n")
                break
            except Exception as e:
                console.print(f"[bold red]Error conectando al servidor:[/] {e}")
                sys.exit(1)

def cmd_help():
    table = Table(title="Available Commands", border_style="bright_black")
    table.add_column("Command", style="bright_green", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_row("networks", "Open networks management menu (List, Add, Update)")
    table.add_row("devices <id>", "List active devices for a network ID")
    table.add_row("scan <id>", "Trigger an active scan for a network ID")
    table.add_row("worst <id>", "Show worst devices by availability")
    table.add_row("me", "Show current user profile")
    table.add_row("register", "Register a new user interactively (Admin only)")
    table.add_row("logout", "Logout from current session")
    table.add_row("clear", "Clear terminal screen")
    table.add_row("exit", "Exit the terminal")
    console.print(table)

def _show_networks_page(page: int, limit: int = 10):
    with console.status(f"[bold green]Fetching networks (Page {page})..."):
        try:
            r = session.get(f"{API_URL}/networks?page={page}&limit={limit}")
            r.raise_for_status()
            response = r.json()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            return None

    networks = response.get("data", [])
    meta = response.get("meta", {})
    
    if not networks:
        console.print("[yellow]No networks found on this page.[/]")
        return meta

    table = Table(title=f"Network List (Page {meta.get('page')} of {meta.get('total_pages')})", border_style="bright_green")
    table.add_column("ID", justify="center", style="cyan")
    table.add_column("CIDR", style="white")
    table.add_column("VLAN", style="grey70")
    table.add_column("Total Devices", justify="center", style="bright_black")
    table.add_column("Alive", justify="center", style="bright_green")
    table.add_column("Down", justify="center", style="bright_red")

    for net in networks:
        counts = net.get("device_counts", {})
        table.add_row(
            str(net["id"]),
            net["cidr"],
            net.get("vlan_name") or "---",
            str(counts.get("total", 0)),
            str(counts.get("alive", 0)),
            str(counts.get("dead", 0))
        )
    console.print(table)
    return meta

def cmd_networks():
    while True:
        console.print("\n[bold cyan]--- Networks Menu ---[/]")
        console.print("1. Listar Redes (Paginado)")
        console.print("2. Añadir Red (Store)")
        console.print("3. Actualizar Red (Update)")
        console.print("4. Volver al menú principal")
        
        choice = Prompt.ask("[bright_green]Selecciona una opción[/]")
        
        if choice == "1":
            page = 1
            while True:
                meta = _show_networks_page(page)
                if not meta:
                    break
                    
                total_pages = meta.get("total_pages", 1)
                
                cmd = Prompt.ask(f"[cyan]Página {page}/{total_pages}[/] | (n) Siguiente, (p) Anterior, (q) Salir al menú de redes", default="q").lower()
                if cmd == 'n' and page < total_pages:
                    page += 1
                elif cmd == 'p' and page > 1:
                    page -= 1
                elif cmd == 'q':
                    break
        elif choice == "2":
            console.print("\n[bold cyan]--- Nueva Red (Store) ---[/]")
            cidr = Prompt.ask("[bright_green]Ingresa el CIDR[/] (ej. 192.168.1.0/24)")
            if not cidr.strip():
                console.print("[red]El CIDR no puede estar vacío.[/]")
                continue
            vlan_id_str = Prompt.ask("[bright_green]VLAN ID[/] (Opcional)", default="")
            scan_interval_str = Prompt.ask("[bright_green]Intervalo de Escaneo en segs[/] (default: 300)", default="300")
            
            try:
                scan_interval = int(scan_interval_str)
            except ValueError:
                scan_interval = 300
                
            payload = {
                "cidr": cidr.strip(),
                "scan_interval": scan_interval
            }
            if vlan_id_str.strip():
                try:
                    payload["vlan_id"] = int(vlan_id_str)
                except ValueError:
                    pass

            with console.status("[bold green]Creando red..."):
                try:
                    r = session.post(f"{API_URL}/networks", json=payload)
                    if r.status_code == 409:
                        console.print(f"[bold red]❌ Error:[/] La red {cidr} ya existe.")
                        continue
                    r.raise_for_status()
                    data = r.json()
                    console.print(f"[bold green]✅ Red {data['cidr']} añadida exitosamente con ID {data['id']}[/]")
                except Exception as e:
                    console.print(f"[bold red]Error al añadir la red:[/] {e}")
        elif choice == "3":
            console.print("\n[bold cyan]--- Actualizar Red (Update) ---[/]")
            net_id = Prompt.ask("[bright_green]Ingresa el ID de la Red a actualizar[/]")
            if not net_id.strip():
                continue
            
            with console.status(f"[bold green]Obteniendo datos de la red {net_id}..."):
                try:
                    r = session.get(f"{API_URL}/networks/{net_id}")
                    r.raise_for_status()
                    net_data = r.json()
                except Exception as e:
                    console.print(f"[bold red]Error al obtener la red:[/] {e}")
                    continue

            console.print(f"[grey70]Red actual: {net_data['cidr']} | VLAN ID: {net_data.get('vlan_id') or '---'} | Intervalo: {net_data['scan_interval']}s[/]")
            
            new_vlan_id = Prompt.ask("[bright_green]Nuevo VLAN ID[/] (Deja vacío para mantener actual)", default=str(net_data.get("vlan_id") or ""))
            new_interval = Prompt.ask("[bright_green]Nuevo Intervalo en segs[/] (Deja vacío para mantener actual)", default=str(net_data["scan_interval"]))
            is_active_str = Prompt.ask("[bright_green]¿Activa?[/] (s/n, Enter para mantener actual)", default="s" if net_data["is_active"] else "n")
            
            try:
                interval_val = int(new_interval)
            except:
                interval_val = net_data["scan_interval"]
                
            is_active_val = is_active_str.lower().strip() == "s"

            payload = {}
            if new_vlan_id != str(net_data.get("vlan_id") or ""):
                payload["vlan_id"] = int(new_vlan_id) if new_vlan_id.strip() else None
            if interval_val != net_data["scan_interval"]:
                payload["scan_interval"] = interval_val
            if is_active_val != net_data["is_active"]:
                payload["is_active"] = is_active_val

            if not payload:
                console.print("[yellow]No se detectaron cambios.[/]")
                continue

            with console.status("[bold green]Actualizando red..."):
                try:
                    r = session.patch(f"{API_URL}/networks/{net_id}", json=payload)
                    r.raise_for_status()
                    console.print(f"[bold green]✅ Red {net_id} actualizada exitosamente.[/]")
                except Exception as e:
                    console.print(f"[bold red]Error al actualizar:[/] {e}")
        elif choice == "4" or choice.lower() == "q":
            break
        else:
            console.print("[red]Opción no válida.[/]")
def cmd_devices(args):
    if not args:
        console.print("[red]Usage:[/] devices <network_id>")
        return
    net_id = args[0]

    with console.status(f"[bold green]Fetching devices for network {net_id}..."):
        try:
            r = session.get(f"{API_URL}/networks/{net_id}/devices?alive_only=false")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            return

    devices = data.get("devices", [])
    if not devices:
        console.print("[yellow]No devices found.[/]")
        return

    table = Table(title=f"Devices (Network {net_id})", border_style="bright_green")
    table.add_column("IP", style="cyan")
    table.add_column("Status", justify="center")
    table.add_column("MAC Address", style="grey70")
    table.add_column("Hostname", style="white")

    for d in devices:
        status_color = "[bright_green]ALIVE[/]" if d["is_alive"] else "[bright_red]DOWN[/]"
        mac = d.get("mac_address") or "---"
        host = d.get("hostname") or "---"
        table.add_row(d["ip"], status_color, mac, host)

    console.print(table)

def cmd_worst(args):
    if not args:
        console.print("[red]Usage:[/] worst <network_id>")
        return
    net_id = args[0]

    with console.status(f"[bold green]Analyzing worst devices for network {net_id}..."):
        try:
            r = session.get(f"{API_URL}/networks/{net_id}/worst?limit=10")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            return

    devices = data.get("devices", [])
    if not devices:
        console.print("[yellow]No stats available yet.[/]")
        return

    table = Table(title=f"Top 10 Worst Devices (Network {net_id})", border_style="bright_red")
    table.add_column("IP", style="cyan")
    table.add_column("Availability", justify="right", style="red")
    table.add_column("Failed Probes", justify="right", style="bright_red")
    table.add_column("Downtime", style="yellow")

    for d in devices:
        table.add_row(
            d["ip"],
            f"{d['availability_percent']}%",
            str(d["failed_probes"]),
            f"{d['total_downtime_seconds']}s"
        )
    console.print(table)

def cmd_scan(args):
    if not args:
        console.print("[red]Usage:[/] scan <network_id>")
        return
    net_id = args[0]

    try:
        # Guardar el ID del último scan antes del trigger
        r_init = session.get(f"{API_URL}/networks/{net_id}/scans")
        r_init.raise_for_status()
        initial_scans = r_init.json().get("scans", [])
        initial_latest_id = initial_scans[0]["id"] if initial_scans else 0

        # Trigger scan
        r = session.post(f"{API_URL}/networks/{net_id}/scan")
        r.raise_for_status()

        # Countdown ETA con polling cada 3 segundos
        with console.status(f"[bold green]Running stealth ping sweep on network {net_id}... (ETA: ~40s)[/]") as status:
            import time
            eta = 40
            poll_counter = 0
            while True:
                time.sleep(1)
                eta = max(0, eta - 1)
                poll_counter += 1
                status.update(f"[bold green]Running stealth ping sweep on network {net_id}... (ETA: ~{eta}s)[/]")

                if poll_counter >= 3:
                    poll_counter = 0
                    r_check = session.get(f"{API_URL}/networks/{net_id}/scans")
                    if r_check.status_code == 200:
                        current_scans = r_check.json().get("scans", [])
                        current_latest_id = current_scans[0]["id"] if current_scans else 0
                        if current_latest_id > initial_latest_id:
                            break

        console.print(f"[bold bright_green]>[/] Scan complete! Displaying fresh results:\n")
        cmd_devices(args)
    except Exception as e:
        console.print(f"[bold red]Error triggering scan:[/] {e}")

def cmd_add_network(args):
    if not args:
        console.print("[red]Usage:[/] add <cidr> [vlan_id]")
        return
    
    cidr = args[0]
    vlan_id = args[1] if len(args) > 1 else None

    try:
        payload = {"cidr": cidr, "scan_interval": 300}
        if vlan_id:
            payload["vlan_id"] = int(vlan_id)
        
        with console.status(f"[bold green]Adding network {cidr}..."):
            r = session.post(f"{API_URL}/networks", json=payload)
            if r.status_code == 409:
                console.print(f"[yellow]Network {cidr} already exists.[/]")
                return
            r.raise_for_status()
            data = r.json()
            console.print(f"[bold bright_green]>[/] Network {cidr} added successfully with ID [cyan]{data['id']}[/]")
            console.print(f"[bright_black]The system has started scanning it in the background.[/]")
    except Exception as e:
        console.print(f"[bold red]Error adding network:[/] {e}")


def cmd_me():
    with console.status("[bold green]Fetching profile..."):
        try:
            r = session.get(f"{API_URL}/auth/me")
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            return
    table = Table(title="My Profile", border_style="bright_green")
    table.add_column("ID", justify="center", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Email", style="grey70")
    table.add_column("Role ID", justify="center", style="bright_black")
    table.add_column("Status", justify="center", style="bright_green")
    table.add_row(
        str(data["id"]), data["name"], data["email"], str(data["role_id"]),
        "Active" if data["status"] else "Inactive"
    )
    console.print(table)

def cmd_logout():
    try:
        session.post(f"{API_URL}/auth/logout")
    except:
        pass
    session.headers.pop("Authorization", None)
    console.print("[bold green]✅ Logged out successfully.[/]")
    do_login()

def cmd_register(args):
    console.print("\n[bold cyan]User Registration[/]")
    email = Prompt.ask("[bright_green]Email[/]")
    password = Prompt.ask("[bright_green]Password[/]", password=True)
    name = Prompt.ask("[bright_green]Name[/]")
    role_id_str = Prompt.ask("[bright_green]Role ID[/] (default: 2)", default="2")
    try:
        role_id = int(role_id_str)
    except ValueError:
        role_id = 2

    with console.status("[bold green]Registering new user..."):
        try:
            r = session.post(f"{API_URL}/auth/register", json={
                "email": email, "password": password, "name": name, "role_id": role_id
            })
            if r.status_code == 403:
                console.print("[bold red]❌ Access Denied:[/] Only Administrators can register new users.")
                return
            elif r.status_code == 400:
                console.print("[bold red]❌ Error:[/] Email already exists.")
                return
            elif r.status_code == 422:
                console.print("[bold red]❌ Error de Validación en Backend:[/] Los campos no pueden estar vacíos o son muy cortos.")
                return
            r.raise_for_status()
            data = r.json()
            console.print(f"[bold green]✅ User '{data['name']}' registered successfully with ID {data['id']}[/]")
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")

def main():
    clear_screen()
    print_banner()
    do_login()

    while True:
        try:
            cmd_input = Prompt.ask("\n[bold bright_green]root@nscan[/] [bright_black]~[/]")
            if not cmd_input.strip():
                continue

            parts = cmd_input.strip().split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd == "help":
                cmd_help()
            elif cmd == "networks":
                cmd_networks()
            elif cmd == "devices":
                cmd_devices(args)
            elif cmd == "worst":
                cmd_worst(args)
            elif cmd == "scan":
                cmd_scan(args)
            elif cmd == "me":
                cmd_me()
            elif cmd == "register":
                cmd_register(args)
            elif cmd == "logout":
                cmd_logout()
            elif cmd == "clear":
                clear_screen()
            elif cmd in ("exit", "quit"):
                console.print("[bright_black]Logging off...[/]")
                sys.exit(0)
            else:
                console.print(f"[red]Unknown command: {cmd}. Type 'help'.[/]")
        except KeyboardInterrupt:
            console.print("\\n[bright_black]Use 'exit' to quit.[/]")
        except EOFError:
            break

if __name__ == "__main__":
    main()
