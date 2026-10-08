import os
import requests
import json
import sys
import urllib3

# Deshabilitar warnings de certificados autofirmados en entorno de desarrollo/laboratorio
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_URL = os.getenv("API_URL", "https://localhost:8443")
CA_CERT_PATH = os.getenv("CA_CERT_PATH", None)
VERIFY_SSL = CA_CERT_PATH if (CA_CERT_PATH and os.path.exists(CA_CERT_PATH)) else False

TOKEN_FILE = ".token"
REFRESH_TOKEN_FILE = ".refresh_token"


def login(username, password):
    try:
        response = requests.post(
            f"{API_URL}/auth/login",
            json={"codigo": username, "password": password, "client": "cli"},
            verify=VERIFY_SSL
        )
        if response.status_code == 200:
            data = response.json()
            access_token = data.get("access_token") or data.get("token")
            refresh_token = data.get("refresh_token")

            with open(TOKEN_FILE, "w") as f:
                f.write(access_token)

            if refresh_token:
                with open(REFRESH_TOKEN_FILE, "w") as f:
                    f.write(refresh_token)

            print("Login exitoso.")
            print(f"Usuario: {username} | Rol: {data.get('rol')} | Nivel: {data.get('nivel', 'N/A')}")
            print("Token de acceso guardado en .token")
        else:
            print(f"Error de login ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Error conectando al API Gateway en {API_URL}: {e}")


def list_slices():
    try:
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
    except FileNotFoundError:
        print("No estás autenticado. Usa 'python cli.py login <codigo> <password>' primero.")
        return

    try:
        response = requests.get(
            f"{API_URL}/slices",
            headers={"Authorization": f"Bearer {token}"},
            verify=VERIFY_SSL
        )
        if response.status_code == 200:
            print("Slices Activos:")
            print(json.dumps(response.json(), indent=2))
        else:
            print(f"Error ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Error conectando al API Gateway: {e}")


def create_slice(json_path):
    try:
        with open(TOKEN_FILE, "r") as f:
            token = f.read().strip()
        with open(json_path, "r") as f:
            payload = json.load(f)
    except FileNotFoundError as e:
        print(f"Archivo no encontrado: {e}")
        return

    try:
        response = requests.post(
            f"{API_URL}/slices",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            verify=VERIFY_SSL
        )
        if response.status_code in [200, 201, 202]:
            print("Solicitud de Slice procesada:")
            print(json.dumps(response.json(), indent=2))
        else:
            print(f"Error ({response.status_code}): {response.text}")
    except Exception as e:
        print(f"Error conectando al API Gateway: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python cli.py [login|list|create]")
        print("  python cli.py login <codigo> <password>")
        print("  python cli.py list")
        print("  python cli.py create <ruta_json>")
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "login" and len(sys.argv) == 4:
        login(sys.argv[2], sys.argv[3])
    elif command == "list":
        list_slices()
    elif command == "create" and len(sys.argv) == 3:
        create_slice(sys.argv[2])
    else:
        print("Comando no reconocido o argumentos faltantes.")
