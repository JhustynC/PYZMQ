import zmq
import threading
import time
from queue import Queue
import sys
import random

# Configuración de red
# SERVIDOR_IP = "0.0.0.0"  # Escuchar en todas las interfaces
# HEARTBEAT_PORT = "5557"

SERVIDOR_PORT = "5555"
CHAT_PORT = "5556"

class Servidor(threading.Thread):
    """Servidor que maneja múltiples clientes simultáneamente"""
    def __init__(self, cola_chat):
        threading.Thread.__init__(self)
        self.activo = True
        self.daemon = True
        self.cola_chat = cola_chat
        self.clientes_conectados = {}
        self.mensajes_procesados = 0
        self.lock = threading.Lock()
        
    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.ROUTER)  # ROUTER maneja múltiples clientes
        socket.bind(f"tcp://*:{SERVIDOR_PORT}")
        socket.setsockopt(zmq.RCVTIMEO, 1000)
        
        print("[SERVIDOR] Iniciado correctamente, esperando conexiones...\n")
        
        while self.activo:
            try:
                # Recibir: [identidad_cliente, vacío, mensaje]
                identidad = socket.recv()
                socket.recv()  # frame vacío
                mensaje = socket.recv_string()
                
                # Procesar comando
                respuesta = self.procesar_comando(identidad, mensaje)
                
                # Enviar respuesta al cliente específico
                socket.send(identidad, zmq.SNDMORE)
                socket.send(b"", zmq.SNDMORE)
                socket.send_string(respuesta)
                
                self.mensajes_procesados += 1
                
            except zmq.Again:
                continue
            except Exception as e:
                print(f"[ERROR] Servidor: {e}")
        
        socket.close()
        context.term()
    
    def procesar_comando(self, identidad, comando):
        """Procesa comandos de los clientes"""
        id_corto = identidad.hex()[:8]
        
        if comando.startswith("/login"):
            partes = comando.split(maxsplit=1)
            if len(partes) == 2:
                nombre = partes[1].strip()
                with self.lock:
                    self.clientes_conectados[identidad] = nombre
                
                # Anunciar al chat
                self.cola_chat.put(f"[SISTEMA] {nombre} se ha conectado al servidor")
                print(f"[CONEXION] Cliente '{nombre}' conectado (ID: {id_corto})")
                return f"[OK] Bienvenido {nombre}. Usuarios conectados: {len(self.clientes_conectados)}"
            return "[ERROR] Uso: /login <tu_nombre>"
        
        elif comando.startswith("/msg"):
            partes = comando.split(maxsplit=1)
            if len(partes) == 2:
                texto = partes[1].strip()
                nombre = self.clientes_conectados.get(identidad, f"Usuario-{id_corto}")
                
                # Enviar mensaje al chat
                self.cola_chat.put(f"[{nombre}] {texto}")
                return "[OK] Mensaje enviado"
            return "[ERROR] Uso: /msg <texto>"
        
        elif comando.startswith("/users"):
            with self.lock:
                if not self.clientes_conectados:
                    return "[INFO] No hay usuarios conectados"
                
                lista = "\n".join([f"  - {nombre}" for nombre in self.clientes_conectados.values()])
                return f"[USUARIOS] Total conectados: {len(self.clientes_conectados)}\n{lista}"
            return "[ERROR] Uso: /users"
        
        elif comando.startswith("/suma"):
            try:
                partes = comando.split()
                a, b = int(partes[1]), int(partes[2])
                resultado = a + b
                nombre = self.clientes_conectados.get(identidad, "Alguien")
                self.cola_chat.put(f"[CALCULO] {nombre} realizo una suma: {a} + {b} = {resultado}")
                return f"[RESULTADO] {a} + {b} = {resultado}"
            except:
                return "[ERROR] Uso: /suma <num1> <num2>"
        
        elif comando.startswith("/hora"):
            return f"[HORA] {time.strftime('%H:%M:%S')}"
        
        elif comando.startswith("/stats"):
            return f"[STATS] Mensajes procesados: {self.mensajes_procesados} | Usuarios activos: {len(self.clientes_conectados)}"
        
        elif comando.startswith("/logout"):
            nombre = self.clientes_conectados.get(identidad, "Usuario")
            with self.lock:
                if identidad in self.clientes_conectados:
                    del self.clientes_conectados[identidad]
            self.cola_chat.put(f"[SISTEMA] {nombre} se ha desconectado")
            return "[OK] Sesion cerrada"
        
        else:
            return "[ERROR] Comando no reconocido. Usa /ayuda para ver opciones"
    
    def detener(self):
        self.activo = False

class ChatBroadcast(threading.Thread):
    """Difunde mensajes del chat a todos los clientes"""
    def __init__(self, cola_chat):
        threading.Thread.__init__(self)
        self.cola_chat = cola_chat
        self.activo = True
        self.daemon = True
        
    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.PUB)
        socket.bind(f"tcp://*:{CHAT_PORT}")
        
        print("[BROADCAST] Sistema de difusion activo\n")
        time.sleep(0.5)
        
        while self.activo:
            if not self.cola_chat.empty():
                mensaje = self.cola_chat.get()
                socket.send_string(f"CHAT:{mensaje}")
                print(f"[BROADCAST] {mensaje}")
            time.sleep(0.1)
        
        socket.close()
        context.term()
    
    def detener(self):
        self.activo = False

class ClienteReceptor(threading.Thread):
    """Thread que escucha mensajes del chat"""
    def __init__(self, ip_servidor):
        threading.Thread.__init__(self)
        self.activo = True
        self.daemon = True
        self.ip_servidor = ip_servidor
        
    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.connect(f"tcp://{self.ip_servidor}:{CHAT_PORT}")
        socket.setsockopt_string(zmq.SUBSCRIBE, "CHAT:")
        socket.setsockopt(zmq.RCVTIMEO, 1000)
        
        while self.activo:
            try:
                mensaje = socket.recv_string()
                # Remover prefijo "CHAT:"
                contenido = mensaje[5:]
                print(f"\n{contenido}")
                print(">> ", end="", flush=True)
            except zmq.Again:
                continue
        
        socket.close()
        context.term()
    
    def detener(self):
        self.activo = False

def enviar_comando_cliente(comando, identidad_cliente, ip_servidor):
    """Envía un comando al servidor usando DEALER (múltiples requests)"""
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.IDENTITY, identidad_cliente)
    socket.connect(f"tcp://{ip_servidor}:{SERVIDOR_PORT}")
    socket.setsockopt(zmq.RCVTIMEO, 5000)
    
    try:
        # Enviar comando
        socket.send(b"", zmq.SNDMORE)
        socket.send_string(comando)
        
        # Recibir respuesta
        socket.recv()  # frame vacío
        respuesta = socket.recv_string()
        print(f"\n{respuesta}")
        
    except zmq.Again:
        print("\n[TIMEOUT] El servidor no respondio a tiempo")
    except Exception as e:
        print(f"\n[ERROR] {e}")
    finally:
        socket.close()
        context.term()

def mostrar_menu():
    """Muestra el menú de comandos"""
    print("\n" + "="*60)
    print("COMANDOS DISPONIBLES:")
    print("="*60)
    print("  /login <nombre>      - Identificarse en el sistema")
    print("  /msg <texto>         - Enviar mensaje al chat")
    print("  /users               - Listar usuarios conectados")
    print("  /suma <n1> <n2>      - Realizar suma (visible para todos)")
    print("  /hora                - Obtener hora actual")
    print("  /stats               - Ver estadisticas del servidor")
    print("  /logout              - Cerrar sesion")
    print("  /ayuda               - Mostrar este menu")
    print("  /salir               - Terminar programa")
    print("="*60 + "\n")

def modo_servidor():
    """Ejecuta el servidor"""
    print("\n" + "="*60)
    print("MODO SERVIDOR - Sistema Multi-Cliente")
    print("="*60 + "\n")
    
    # Mostrar IP del servidor
    import socket
    try:
        hostname = socket.gethostname()
        ip_local = socket.gethostbyname(hostname)
        print(f"[INFO] Direccion IP del servidor: {ip_local}")
        print(f"[INFO] Los clientes deben conectarse a: {ip_local}\n")
    except:
        print("[ADVERTENCIA] No se pudo determinar la IP automaticamente")
        print("[INFO] Usa 'ipconfig' (Windows) o 'ifconfig' (Linux/Mac)\n")
    
    cola_chat = Queue()
    
    servidor = Servidor(cola_chat)
    chat_broadcast = ChatBroadcast(cola_chat)
    
    servidor.start()
    chat_broadcast.start()
    
    print("[OK] Servidor en ejecucion")
    print("[INFO] Presiona Ctrl+C para detener\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n[STOP] Deteniendo servidor...")
        servidor.detener()
        chat_broadcast.detener()
        time.sleep(1)
        print("[OK] Servidor detenido correctamente\n")

def modo_cliente():
    """Ejecuta un cliente"""
    # Generar identidad única para este cliente
    identidad = f"cliente-{random.randint(1000, 9999)}".encode()
    
    print("\n" + "="*60)
    print(f"MODO CLIENTE - ID: {identidad.decode()}")
    print("="*60 + "\n")
    
    # Pedir IP del servidor
    print("[CONFIG] Ingresa la direccion IP del servidor:")
    print("  - Conexion local: localhost o 127.0.0.1")
    print("  - Conexion remota: ejemplo 192.168.1.100")
    ip_servidor = input("\nIP del servidor: ").strip()
    
    if not ip_servidor:
        ip_servidor = "localhost"
        print(f"[INFO] Usando: {ip_servidor}\n")
    
    print("\n[NOTA] Usa /login <nombre> para identificarte primero\n")
    
    # Iniciar thread que escucha el chat
    receptor = ClienteReceptor(ip_servidor)
    receptor.start()
    
    time.sleep(0.5)
    mostrar_menu()
    
    try:
        while True:
            comando = input(">> ").strip()
            
            if not comando:
                continue
            
            if comando == "/salir":
                enviar_comando_cliente("/logout", identidad, ip_servidor)
                print("\n[INFO] Cerrando cliente...")
                break
            
            if comando == "/ayuda":
                mostrar_menu()
                continue
            
            enviar_comando_cliente(comando, identidad, ip_servidor)
            
    except KeyboardInterrupt:
        print("\n\n[INTERRUPT] Interrupcion detectada")
    finally:
        receptor.detener()
        time.sleep(0.5)
        print("[OK] Cliente desconectado\n")

def main():
    print("\n" + "="*60)
    print("SISTEMA MULTI-CLIENTE CON PYZMQ Y THREADS")
    print("="*60)
    print("\nSelecciona el modo de ejecucion:")
    print("  1) Servidor (iniciar primero)")
    print("  2) Cliente (conectar a servidor existente)")
    print("="*60)
    
    opcion = input("\nOpcion (1 o 2): ").strip()
    
    if opcion == "1":
        modo_servidor()
    elif opcion == "2":
        modo_cliente()
    else:
        print("[ERROR] Opcion invalida")

if __name__ == "__main__":
    main()