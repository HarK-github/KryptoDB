import argparse
import httpx

def main():
    parser = argparse.ArgumentParser(prog="synapse")
    subparsers = parser.add_subparsers(dest="command")
    
    session_parser = subparsers.add_parser("session")
    session_subparsers = session_parser.add_subparsers(dest="session_command")
    
    start_parser = session_subparsers.add_parser("start")
    start_parser.add_argument("--active-partitions", nargs="+", default=[])
    
    show_parser = session_subparsers.add_parser("show")
    
    args = parser.parse_args()
    
    if args.command == "session":
        if args.session_command == "start":
            response = httpx.post(
                "http://127.0.0.1:8000/session",
                json={"active_partitions": args.active_partitions}
            )
            print(f"Session started. Active partitions: {response.json().get('active_partitions')}")
            
        elif args.session_command == "show":
            response = httpx.get("http://127.0.0.1:8000/session")
            print(f"Active partitions: {response.json().get('active_partitions')}")

if __name__ == "__main__":
    main()
