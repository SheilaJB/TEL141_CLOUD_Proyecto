import requests
import json
import sys

API_URL = "http://localhost:8000"
TOKEN_FILE = ".token"

def login(username, password):
    try:
        response = requests.post(f"{API_URL}/login", json={"username": username, "password": password})
        if response.status_code == 200:
            token = response.json().get("token")
            with open(TOKEN_FILE, "w") as f:
                f.write(token)
            print("Login exitoso. Token guardado.")
        else:
            print(f"Error de login: {response.text}")
    except Exception as e:
        print(f"Error conectando al API Gateway: {e}")

def list_slices():
    try:
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
    except FileNotFoundError:
        print("No estás logueado. Usa 'python cli.py login'")
        return

    response = requests.get(f"{API_URL}/slices", headers={"Authorization": f"Bearer {token}"})
    if response.status_code == 200:
        print("Slices Activos:")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: {response.text}")

def create_slice(json_path):
    try:
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
        with open(json_path, "r") as f:
            payload = json.load(f)
    except FileNotFoundError as e:
        print(f"Archivo no encontrado: {e}")
        return

    response = requests.post(f"{API_URL}/slices", json=payload, headers={"Authorization": f"Bearer {token}"})
    if response.status_code in [200, 201, 202]:
        print("Solicitud de Slice enviada:")
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: {response.text}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python cli.py [login|list|create]")
        sys.exit(1)

    command = sys.argv[1]

    if command == "login" and len(sys.argv) == 4:
        login(sys.argv[2], sys.argv[3])
    elif command == "list":
        list_slices()
    elif command == "create" and len(sys.argv) == 3:
        create_slice(sys.argv[2])
    else:
        print("Comando no reconocido o argumentos faltantes.")
