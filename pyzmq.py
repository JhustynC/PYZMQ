import zmq
import threading
import time
from queue import Queue
import sys
import random

# Puertos de comunicación
SERVIDOR_PORT = "5555"
CHAT_PORT = "5556"
HEARTBEAT_PORT = "5557"

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
        
        print("🟢 [SERVIDOR] Listo para múltiples clientes\n")
        
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
                print(f"❌ [SERVIDOR] Error: {e}")
        
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
                self.cola_chat.put(f"🎉 {nombre} se ha conectado!")
                print(f"👤 [SERVIDOR] Cliente '{nombre}' conectado ({id_corto})")
                return f"✅ Bienvenido {nombre}! Hay {len(self.clientes_conectados)} usuarios conectados"
            return "❌ Usa: /login <tu_nombre>"
        
        elif comando.startswith("/msg"):
            partes = comando.split(maxsplit=1)
            if len(partes) == 2:
                texto = partes[1].strip()
                nombre = self.clientes_conectados.get(identidad, f"Usuario-{id_corto}")
                
                # Enviar mensaje al chat
                self.cola_chat.put(f"💬 {nombre}: {texto}")
                return "✅ Mensaje enviado al chat"
            return "❌ Usa: /msg <texto>"
        
        elif comando.startswith("/users"):
            with self.lock:
                if not self.clientes_conectados:
                    return "📭 No hay usuarios conectados"
                
                lista = "\n".join([f"  👤 {nombre}" for nombre in self.clientes_conectados.values()])
                return f"👥 Usuarios conectados ({len(self.clientes_conectados)}):\n{lista}"
        
        elif comando.startswith("/suma"):
            try:
                partes = comando.split()
                a, b = int(partes[1]), int(partes[2])
                resultado = a + b
                nombre = self.clientes_conectados.get(identidad, "Alguien")
                self.cola_chat.put(f"🔢 {nombre} calculó: {a} + {b} = {resultado}")
                return f"✅ Resultado: {resultado}"
            except:
                return "❌ Usa: /suma <num1> <num2>"
        
        elif comando.startswith("/hora"):
            return f"🕐 Hora: {time.strftime('%H:%M:%S')}"
        
        elif comando.startswith("/stats"):
            return f"📊 Mensajes: {self.mensajes_procesados} | Usuarios: {len(self.clientes_conectados)}"
        
        elif comando.startswith("/logout"):
            nombre = self.clientes_conectados.get(identidad, "Usuario")
            with self.lock:
                if identidad in self.clientes_conectados:
                    del self.clientes_conectados[identidad]
            self.cola_chat.put(f"👋 {nombre} se ha desconectado")
            return "✅ Hasta luego!"
        
        else:
            return "❓ Comando desconocido. Usa /ayuda"
    
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
        
        print("📻 [CHAT] Canal de difusión activo\n")
        time.sleep(0.5)
        
        while self.activo:
            if not self.cola_chat.empty():
                mensaje = self.cola_chat.get()
                socket.send_string(f"CHAT:{mensaje}")
                print(f"📢 {mensaje}")
            time.sleep(0.1)
        
        socket.close()
        context.term()
    
    def detener(self):
        self.activo = False

class ClienteReceptor(threading.Thread):
    """Thread que escucha mensajes del chat"""
    def __init__(self):
        threading.Thread.__init__(self)
        self.activo = True
        self.daemon = True
        
    def run(self):
        context = zmq.Context()
        socket = context.socket(zmq.SUB)
        socket.connect(f"tcp://localhost:{CHAT_PORT}")
        socket.setsockopt_string(zmq.SUBSCRIBE, "CHAT:")
        socket.setsockopt(zmq.RCVTIMEO, 1000)
        
        while self.activo:
            try:
                mensaje = socket.recv_string()
                # Remover prefijo "CHAT:"
                contenido = mensaje[5:]
                print(f"\n{contenido}")
                print("💻 Comando: ", end="", flush=True)
            except zmq.Again:
                continue
        
        socket.close()
        context.term()
    
    def detener(self):
        self.activo = False

def enviar_comando_cliente(comando, identidad_cliente):
    """Envía un comando al servidor usando DEALER (múltiples requests)"""
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.IDENTITY, identidad_cliente)
    socket.connect(f"tcp://localhost:{SERVIDOR_PORT}")
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
        print("\n⏱️ Timeout: El servidor no respondió")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        socket.close()
        context.term()

def mostrar_menu():
    """Muestra el menú de comandos"""
    print("\n" + "="*60)
    print("📋 COMANDOS DISPONIBLES:")
    print("="*60)
    print("  /login <nombre>      - Identificarte en el chat")
    print("  /msg <texto>         - Enviar mensaje al chat público")
    print("  /users               - Ver usuarios conectados")
    print("  /suma <n1> <n2>      - Sumar números (todos lo ven)")
    print("  /hora                - Ver hora actual")
    print("  /stats               - Estadísticas del servidor")
    print("  /logout              - Desconectarse")
    print("  /ayuda               - Mostrar este menú")
    print("  /salir               - Salir del programa")
    print("="*60 + "\n")

def modo_servidor():
    """Ejecuta el servidor"""
    print("\n" + "="*60)
    print("🖥️  MODO SERVIDOR - Sistema Multi-Cliente")
    print("="*60 + "\n")
    
    cola_chat = Queue()
    
    servidor = Servidor(cola_chat)
    chat_broadcast = ChatBroadcast(cola_chat)
    
    servidor.start()
    chat_broadcast.start()
    
    print("✅ Servidor iniciado. Los clientes pueden conectarse ahora.")
    print("   Presiona Ctrl+C para detener\n")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Deteniendo servidor...")
        servidor.detener()
        chat_broadcast.detener()
        time.sleep(1)
        print("✅ Servidor detenido\n")

def modo_cliente():
    """Ejecuta un cliente"""
    # Generar identidad única para este cliente
    identidad = f"cliente-{random.randint(1000, 9999)}".encode()
    
    print("\n" + "="*60)
    print(f"👤 MODO CLIENTE - ID: {identidad.decode()}")
    print("="*60 + "\n")
    print("💡 Primero usa /login <tu_nombre> para identificarte\n")
    
    # Iniciar thread que escucha el chat
    receptor = ClienteReceptor()
    receptor.start()
    
    time.sleep(0.5)
    mostrar_menu()
    
    try:
        while True:
            comando = input("💻 Comando: ").strip()
            
            if not comando:
                continue
            
            if comando == "/salir":
                enviar_comando_cliente("/logout", identidad)
                print("\n👋 Cerrando cliente...")
                break
            
            if comando == "/ayuda":
                mostrar_menu()
                continue
            
            enviar_comando_cliente(comando, identidad)
            
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupción detectada")
    finally:
        receptor.detener()
        time.sleep(0.5)
        print("✅ Cliente desconectado\n")

def main():
    print("\n" + "="*60)
    print("🚀 SISTEMA MULTI-CLIENTE CON PYZMQ Y THREADS")
    print("="*60)
    print("\nElige el modo:")
    print("  1) 🖥️  Servidor (ejecuta primero)")
    print("  2) 👤 Cliente (ejecuta en otra terminal)")
    print("="*60)
    
    opcion = input("\nOpción (1 o 2): ").strip()
    
    if opcion == "1":
        modo_servidor()
    elif opcion == "2":
        modo_cliente()
    else:
        print("❌ Opción inválida")

if __name__ == "__main__":
    main()