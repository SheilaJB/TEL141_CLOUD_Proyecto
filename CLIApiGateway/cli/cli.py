import os
import sys
import json
import urllib3
import requests

# Supresión de advertencias SSL para entornos de desarrollo
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configuración base
API_URL = os.getenv("API_URL", "https://localhost:8443")
VERIFY_SSL = False
TOKEN = None
CURRENT_USER = None
CURRENT_ROLE = None

# ==========================================
# UTILIDADES CORE Y RED
# ==========================================

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def print_header(title):
    clear_screen()
    print("=" * 60)
    print(f" {title}".upper())
    print("=" * 60)
    print()

def pause():
    print("\nPresione Enter para continuar...")
    input()

def print_json(data):
    print(json.dumps(data, indent=2))

def make_request(method, endpoint, **kwargs):
    headers = kwargs.pop("headers", {})
    if TOKEN and endpoint != "/auth/login":
        headers["Authorization"] = f"Bearer {TOKEN}"
    
    url = f"{API_URL}{endpoint}"
    try:
        response = requests.request(method, url, headers=headers, verify=VERIFY_SSL, **kwargs)
        if response.status_code in (200, 201, 202, 204):
            if response.text:
                try:
                    return True, response.json()
                except json.JSONDecodeError:
                    return True, {"message": response.text}
            return True, {}
        else:
            try:
                return False, response.json()
            except json.JSONDecodeError:
                return False, {"error": response.text}
    except requests.exceptions.RequestException as e:
        return False, {"error": f"Fallo de conexion: {str(e)}"}

# ==========================================
# GESTIÓN DE AUTENTICACIÓN
# ==========================================

def login():
    global TOKEN, CURRENT_USER, CURRENT_ROLE
    print_header("Orquestador Cloud - Autenticacion de Sistema")
    print("Ingrese sus credenciales de acceso.")
    
    username = input("Codigo de usuario: ").strip()
    password = input("Contrasena: ").strip()
    
    success, data = make_request("POST", "/auth/login", json={"codigo": username, "password": password, "client": "cli"})
    
    if success:
        TOKEN = data.get("access_token") or data.get("token")
        CURRENT_USER = username
        CURRENT_ROLE = str(data.get("rol", "usuario")).lower()
        print("\nAutenticacion exitosa.")
        pause()
    else:
        print(f"\nError de autenticacion: {data.get('error', 'Credenciales invalidas o error de servidor')}")
        pause()
        sys.exit(1)

# ==========================================
# MENÚS Y FLUJOS: ROL USUARIO
# ==========================================

def menu_usuario():
    while True:
        print_header(f"Panel de Usuario - {CURRENT_USER}")
        print("1. Crear Slice desde archivo JSON")
        print("2. Validar Topologia de un Slice")
        print("3. Planificar / Vista previa de Recursos")
        print("4. Solicitar Despliegue de Slice (Deploy)")
        print("5. Ver Estado de mis Slices")
        print("6. Eliminar Version de un Slice")
        print("0. Salir del Sistema")
        
        opcion = input("\nSeleccione una opcion: ").strip()
        
        if opcion == "1":
            crear_slice()
        elif opcion == "2":
            validar_slice()
        elif opcion == "3":
            planificar_slice()
        elif opcion == "4":
            desplegar_slice()
        elif opcion == "5":
            listar_slices()
        elif opcion == "6":
            eliminar_slice()
        elif opcion == "0":
            break
        else:
            print("Opcion no valida.")
            pause()

def crear_slice():
    print_header("Crear Nuevo Slice desde JSON")
    filepath = input("Ingrese la ruta del archivo JSON con la topologia: ").strip()
    
    if not os.path.exists(filepath):
        print("\nError: Archivo no encontrado.")
        pause()
        return
        
    try:
        with open(filepath, "r") as f:
            payload = json.load(f)
    except Exception as e:
        print(f"\nError leyendo el archivo JSON: {e}")
        pause()
        return
        
    print("\nProcesando creacion de slice...")
    success, data = make_request("POST", "/slices", json=payload)
    if success:
        print("\nSlice creado exitosamente:")
        print_json(data)
    else:
        print("\nError al crear el slice:")
        print_json(data)
    pause()

def validar_slice():
    print_header("Validar Topologia de Slice")
    slice_id = input("Ingrese el ID del Slice: ").strip()
    version = input("Ingrese el numero de version (default 1): ").strip() or "1"
    
    print(f"\nValidando Slice {slice_id} v{version}...")
    success, data = make_request("POST", f"/slices/{slice_id}/versions/{version}/validate")
    if success:
        print("\nResultado de validacion exitoso:")
        print_json(data)
    else:
        print("\nError de validacion:")
        print_json(data)
    pause()

def planificar_slice():
    print_header("Planificar / Vista Previa de Recursos")
    slice_id = input("Ingrese el ID del Slice: ").strip()
    version = input("Ingrese el numero de version (default 1): ").strip() or "1"
    
    print(f"\nGenerando plan de ejecucion para Slice {slice_id} v{version}...")
    success, data = make_request("POST", f"/slices/{slice_id}/versions/{version}/plan")
    if success:
        print("\nPlan de despliegue generado:")
        print_json(data)
    else:
        print("\nError al generar plan:")
        print_json(data)
    pause()

def desplegar_slice():
    print_header("Solicitar Despliegue de Slice")
    slice_id = input("Ingrese el ID del Slice: ").strip()
    version = input("Ingrese el numero de version (default 1): ").strip() or "1"
    
    print(f"\nIniciando solicitud de despliegue para Slice {slice_id} v{version}...")
    success, data = make_request("POST", f"/slices/{slice_id}/versions/{version}/deploy")
    if success:
        print("\nDespliegue procesado / encolado:")
        print_json(data)
    else:
        print("\nError al solicitar despliegue:")
        print_json(data)
    pause()

def listar_slices():
    print_header("Inventario de Slices")
    success, data = make_request("GET", "/slices")
    if success:
        print_json(data)
    else:
        print("Error al obtener el inventario de slices:")
        print_json(data)
    pause()

def eliminar_slice():
    print_header("Eliminar Version de Slice")
    slice_id = input("Ingrese el ID del Slice: ").strip()
    version = input("Ingrese el numero de version a retirar (default 1): ").strip() or "1"
    confirmacion = input(f"¿Esta seguro de retirar la version {version} del Slice {slice_id}? (S/N): ").strip().upper()
    
    if confirmacion == "S":
        print("\nSolicitando eliminacion...")
        success, data = make_request("DELETE", f"/slices/{slice_id}/versions/{version}")
        if success:
            print("\nVersion de Slice eliminada exitosamente.")
        else:
            print("\nError eliminando la version del Slice:")
            print_json(data)
    pause()

# ==========================================
# MENÚS Y FLUJOS: ROL OPERADOR
# ==========================================

def menu_operador():
    while True:
        print_header(f"Panel de Operador de Plataforma - {CURRENT_USER}")
        print("1. Consultar Slices Registrados")
        print("2. Evaluar Despliegue (Aprobar/Rechazar)")
        print("0. Salir del Sistema")
        
        opcion = input("\nSeleccione una opcion: ").strip()
        
        if opcion == "1":
            listar_slices()
        elif opcion == "2":
            evaluar_solicitud()
        elif opcion == "0":
            break
        else:
            print("Opcion no valida.")
            pause()

def evaluar_solicitud():
    print_header("Evaluacion de Despliegue por Operador")
    deployment_id = input("Ingrese el ID del Despliegue (Deployment ID): ").strip()
    decision = input("Decision (A para Aprobar, R para Rechazar): ").strip().upper()
    
    if decision not in ["A", "R"]:
        print("Decision invalida.")
        pause()
        return
        
    motivo = input("Ingrese el motivo o comentario de auditoria: ").strip()
    if not motivo:
        motivo = "Evaluado por Operador desde CLI"
        
    is_approved = (decision == "A")
    payload = {
        "approved": is_approved,
        "reason": motivo
    }
    
    print("\nProcesando evaluacion en el API Gateway...")
    success, data = make_request("POST", f"/slices/deployments/{deployment_id}/approve", json=payload)
    if success:
        estado_str = "APROBADO" if is_approved else "RECHAZADO"
        print(f"\nDespliegue {deployment_id} procesado correctamente como {estado_str}:")
        print_json(data)
    else:
        print("\nFallo en la evaluacion del despliegue:")
        print_json(data)
    pause()

# ==========================================
# MENÚS Y FLUJOS: ROL ADMINISTRADOR
# ==========================================

def menu_administrador():
    while True:
        print_header(f"Panel de Administracion de Infraestructura - {CURRENT_USER}")
        print("1. Gestion de Usuarios y Roles")
        print("2. Consultar Catalogo de Flavors")
        print("3. Consultar Catalogo de Imagenes Base")
        print("4. Gestion de Zonas de Disponibilidad")
        print("0. Salir del Sistema")
        
        opcion = input("\nSeleccione una opcion: ").strip()
        
        if opcion == "1":
            admin_consultar("users", "Listado de Usuarios Registrados")
        elif opcion == "2":
            admin_consultar("flavors", "Catalogo Oficial de Flavors")
        elif opcion == "3":
            admin_consultar("images", "Catalogo Maestro de Imagenes")
        elif opcion == "4":
            admin_consultar("zones", "Estado de Zonas de Disponibilidad")
        elif opcion == "0":
            break
        else:
            print("Opcion no valida.")
            pause()

def admin_consultar(entidad, titulo):
    print_header(titulo)
    print("Consultando la base de datos central...")
    success, data = make_request("GET", f"/admin/{entidad}")
    if success:
        print_json(data)
    else:
        print("Error en la extraccion de datos:")
        print_json(data)
    pause()

# ==========================================
# ENTRY POINT PRINCIPAL
# ==========================================

def main():
    try:
        login()
        
        # Enrutamiento de interfaz basado en el rol de sistema validado
        if CURRENT_ROLE == "operador":
            menu_operador()
        elif CURRENT_ROLE == "admin" or CURRENT_ROLE == "administrador":
            menu_administrador()
        else:
            # Por defecto y mecanismo de seguridad, se asigna rol de consumidor final
            menu_usuario()
            
    except KeyboardInterrupt:
        print("\n\nCierre de sesion forzado. Hasta luego.")
        sys.exit(0)

if __name__ == "__main__":
    main()
