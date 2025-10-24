# **EXPLICACIÓN COMPLETA DEL SISTEMA**

## **PARTE 1: CONCEPTOS FUNDAMENTALES**

### **1.1 ¿Qué es un Thread (Hilo)?**

Imagina que tienes 4 tareas:
- Tarea A: Escuchar si llegan mensajes
- Tarea B: Enviar mensajes
- Tarea C: Procesar datos
- Tarea D: Mostrar información en pantalla

**Sin threads:** Tendrías que hacer una, esperar que termine, luego la siguiente, etc. Si la Tarea A está esperando un mensaje, las demás NO pueden funcionar.

**Con threads:** Cada tarea corre en su propio "trabajador" simultáneamente. Mientras el Thread A espera mensajes, el Thread B puede estar enviando, el Thread C procesando, etc.

```python
class MiThread(threading.Thread):
    def run(self):
        # Código que se ejecutará en paralelo
        while True:
            hacer_algo()
```

### **1.2 ¿Qué es una Queue (Cola)?**

Es una estructura de datos tipo "fila de banco": el primero en entrar es el primero en salir (FIFO).

**¿Por qué usarlas con threads?**
Los threads NO pueden compartir variables normales de forma segura. Si dos threads modifican la misma variable al mismo tiempo, se corrompe. Las colas son **thread-safe** (seguras para usar entre threads).

```python
cola = Queue()

# Thread A pone datos
cola.put("mensaje")

# Thread B saca datos
dato = cola.get()  # Obtiene "mensaje"
```

### **1.3 ¿Qué es ZMQ?**

ZeroMQ es una librería de mensajería que permite comunicación entre procesos/programas/computadoras.

**Patrones principales:**

1. **ROUTER-DEALER**: Cliente-Servidor donde el servidor puede manejar múltiples clientes
   - ROUTER (servidor): Puede enviar a un cliente específico
   - DEALER (cliente): Puede enviar múltiples peticiones

2. **PUB-SUB**: Publicación-Suscripción
   - PUB (publicador): Envía mensajes a todos
   - SUB (suscriptor): Recibe solo los que le interesan

---

## **PARTE 2: ARQUITECTURA DEL SISTEMA**

El sistema tiene **5 componentes principales**:

```
SERVIDOR (PC 1)
├── Thread 1: Servidor (ROUTER)    - Recibe comandos de clientes
├── Thread 2: ChatBroadcast (PUB)  - Difunde mensajes a todos
└── Cola: cola_chat                - Comunica Thread 1 y Thread 2

CLIENTE (PC 2, 3, 4...)
├── Thread 1: Main (DEALER)        - Envía comandos al servidor
├── Thread 2: ClienteReceptor (SUB) - Escucha mensajes del chat
└── Interacción por terminal
```

---

## **PARTE 3: CÓDIGO DETALLADO**

### **3.1 CLASE SERVIDOR (Thread principal del servidor)**

```python
class Servidor(threading.Thread):
    def __init__(self, cola_chat):
        threading.Thread.__init__(self)  # Inicializar como thread
        self.activo = True                # Bandera para detener el thread
        self.daemon = True                # Se cierra cuando termine el programa
        self.cola_chat = cola_chat        # Cola para enviar mensajes al broadcast
        self.clientes_conectados = {}     # Diccionario: identidad -> nombre
        self.mensajes_procesados = 0      # Contador de mensajes
        self.lock = threading.Lock()      # Para acceso seguro al diccionario
```

**¿Qué hace cada atributo?**

- `self.activo`: Controla el bucle principal. Cuando sea `False`, el thread termina
- `self.daemon = True`: Si el programa principal termina, este thread también (no queda colgado)
- `self.cola_chat`: Referencia a la cola compartida con ChatBroadcast
- `self.clientes_conectados`: Guarda qué clientes están conectados y sus nombres
- `self.lock`: Un "candado" para que solo un thread modifique el diccionario a la vez

```python
def run(self):
    context = zmq.Context()
    socket = context.socket(zmq.ROUTER)
    socket.bind(f"tcp://*:{SERVIDOR_PORT}")
    socket.setsockopt(zmq.RCVTIMEO, 1000)
```

**Línea por línea:**

1. `context = zmq.Context()`: Crea el contexto de ZMQ (contenedor de todos los sockets)
2. `socket = zmq.ROUTER`: Crea un socket tipo ROUTER (puede manejar múltiples clientes)
3. `socket.bind("tcp://*:5555")`: Se "amarra" al puerto 5555, esperando conexiones
   - `*` significa "todas las interfaces de red" (0.0.0.0)
4. `socket.setsockopt(zmq.RCVTIMEO, 1000)`: Timeout de 1 segundo para recibir mensajes
   - Si no hay mensajes en 1 segundo, lanza excepción `zmq.Again`
   - Esto permite verificar `self.activo` periódicamente

```python
while self.activo:
    try:
        identidad = socket.recv()      # ID único del cliente
        socket.recv()                  # Frame vacío (protocolo ROUTER)
        mensaje = socket.recv_string() # El mensaje real
        
        respuesta = self.procesar_comando(identidad, mensaje)
        
        socket.send(identidad, zmq.SNDMORE)  # Enviar a ese cliente
        socket.send(b"", zmq.SNDMORE)        # Frame vacío
        socket.send_string(respuesta)         # La respuesta
```

**¿Por qué tres partes en recv/send?**

El protocolo ROUTER-DEALER usa "frames" (marcos):
- **Frame 1**: Identidad del cliente (ZMQ la asigna automáticamente)
- **Frame 2**: Delimitador vacío (separador)
- **Frame 3**: El mensaje real

Cuando envías de vuelta, ROUTER usa la identidad para saber a qué cliente específico enviar.

```python
def procesar_comando(self, identidad, comando):
    if comando.startswith("/login"):
        partes = comando.split(maxsplit=1)
        if len(partes) == 2:
            nombre = partes[1].strip()
            
            with self.lock:  # <-- IMPORTANTE
                self.clientes_conectados[identidad] = nombre
            
            self.cola_chat.put(f"[SISTEMA] {nombre} se ha conectado")
            return f"[OK] Bienvenido {nombre}"
```

**¿Por qué `with self.lock`?**

Porque varios threads pueden intentar modificar `self.clientes_conectados` al mismo tiempo:
- Thread del Servidor modifica cuando alguien hace `/login`
- Otro comando `/users` lee el diccionario

Sin el lock, podrían corromper los datos. El lock asegura que **solo un thread a la vez** acceda al diccionario.

**¿Por qué `self.cola_chat.put(...)`?**

El Servidor NO envía directamente por broadcast. En su lugar:
1. Pone el mensaje en la **cola**
2. El thread ChatBroadcast **lee de la cola**
3. ChatBroadcast lo envía a todos

Esto **desacopla** los componentes: el Servidor no necesita saber cómo funciona el broadcast.

---

### **3.2 CLASE CHATBROADCAST (Thread de difusión)**

```python
class ChatBroadcast(threading.Thread):
    def __init__(self, cola_chat):
        threading.Thread.__init__(self)
        self.cola_chat = cola_chat  # Misma cola que el Servidor
        self.activo = True
        self.daemon = True
```

```python
def run(self):
    context = zmq.Context()
    socket = context.socket(zmq.PUB)  # Publisher (publicador)
    socket.bind(f"tcp://*:{CHAT_PORT}")
    
    time.sleep(0.5)  # Dar tiempo a que los suscriptores se conecten
    
    while self.activo:
        if not self.cola_chat.empty():    # ¿Hay mensajes en la cola?
            mensaje = self.cola_chat.get()  # Sacar mensaje
            socket.send_string(f"CHAT:{mensaje}")  # Enviar a todos
        time.sleep(0.1)  # Pequeña pausa para no saturar CPU
```

**Flujo completo:**

1. Un cliente hace `/msg Hola`
2. **Servidor** recibe el comando
3. **Servidor** hace `cola_chat.put("[Juan] Hola")`
4. **ChatBroadcast** detecta que hay algo en la cola
5. **ChatBroadcast** hace `get()` para sacar el mensaje
6. **ChatBroadcast** lo envía por ZMQ a todos los suscriptores

**¿Por qué `CHAT:` como prefijo?**

Los suscriptores (clientes) pueden filtrar mensajes. Al usar un prefijo, podríamos tener múltiples canales:
- `CHAT:` para mensajes del chat
- `ALERT:` para alertas del sistema
- `STATS:` para estadísticas

---

### **3.3 CLASE CLIENTERECEPTOR (Thread que escucha en el cliente)**

```python
class ClienteReceptor(threading.Thread):
    def __init__(self, ip_servidor):
        threading.Thread.__init__(self)
        self.activo = True
        self.daemon = True
        self.ip_servidor = ip_servidor
```

```python
def run(self):
    context = zmq.Context()
    socket = context.socket(zmq.SUB)  # Suscriptor
    socket.connect(f"tcp://{self.ip_servidor}:{CHAT_PORT}")
    socket.setsockopt_string(zmq.SUBSCRIBE, "CHAT:")  # Solo mensajes con "CHAT:"
    socket.setsockopt(zmq.RCVTIMEO, 1000)
    
    while self.activo:
        try:
            mensaje = socket.recv_string()
            contenido = mensaje[5:]  # Quitar "CHAT:"
            print(f"\n{contenido}")
            print(">> ", end="", flush=True)  # Re-imprimir el prompt
```

**¿Por qué es un thread separado?**

Porque necesitas hacer DOS cosas al mismo tiempo:
1. **Escribir** comandos en la terminal (thread principal)
2. **Escuchar** mensajes del servidor (este thread)

Si no fuera un thread, cuando escribes algo, no podrías recibir mensajes hasta que termines.

**Diagrama temporal:**

```
Tiempo  Thread Principal        ClienteReceptor
0s      input(">> ")            [esperando mensaje]
2s      [usuario escribiendo]   [mensaje llega!]
2s      [usuario escribiendo]   print("Juan: Hola")
2s      [usuario escribiendo]   print(">> ")
3s      [usuario presiona Enter]
```

---

### **3.4 FUNCIÓN enviar_comando_cliente**

```python
def enviar_comando_cliente(comando, identidad_cliente, ip_servidor):
    context = zmq.Context()
    socket = context.socket(zmq.DEALER)
    socket.setsockopt(zmq.IDENTITY, identidad_cliente)  # Identificarse
    socket.connect(f"tcp://{ip_servidor}:{SERVIDOR_PORT}")
    socket.setsockopt(zmq.RCVTIMEO, 5000)
```

**¿Por qué DEALER?**

- **REQ** (Request): Solo puede enviar 1 mensaje, esperar respuesta, enviar otro
- **DEALER**: Puede enviar múltiples mensajes sin esperar

En este caso, aunque enviamos uno a la vez, DEALER es más flexible y funciona mejor con ROUTER.

```python
socket.send(b"", zmq.SNDMORE)  # Frame vacío
socket.send_string(comando)     # El comando
```

**Protocolo DEALER-ROUTER:**
- DEALER envía: [vacío, mensaje]
- ROUTER recibe: [identidad_dealer, vacío, mensaje]
- ROUTER envía: [identidad_dealer, vacío, respuesta]
- DEALER recibe: [vacío, respuesta]

La identidad la añade automáticamente ROUTER.

---

## **PARTE 4: FLUJO COMPLETO DE UN MENSAJE**

Vamos a seguir un mensaje desde que lo escribes hasta que todos lo ven:

### **Escenario:** Juan escribe `/msg Hola a todos`

**PASO 1: Cliente de Juan (PC 2)**
```python
comando = input(">> ")  # Juan escribe "/msg Hola a todos"
enviar_comando_cliente(comando, identidad, ip_servidor)
```

**PASO 2: Función enviar_comando_cliente**
```python
socket = zmq.DEALER
socket.connect("tcp://192.168.1.100:5555")  # Conecta al servidor
socket.send(b"", zmq.SNDMORE)
socket.send_string("/msg Hola a todos")  # Envía el comando
```

**PASO 3: Servidor (PC 1) - Thread Servidor**
```python
# Recibe en socket ROUTER
identidad = socket.recv()  # ID de Juan
socket.recv()              # Frame vacío
mensaje = socket.recv_string()  # "/msg Hola a todos"

# Procesa
respuesta = self.procesar_comando(identidad, mensaje)
  # Dentro de procesar_comando:
  nombre = self.clientes_conectados[identidad]  # "Juan"
  self.cola_chat.put("[Juan] Hola a todos")     # <-- Pone en cola
  return "[OK] Mensaje enviado"

# Responde a Juan
socket.send(identidad, zmq.SNDMORE)
socket.send(b"", zmq.SNDMORE)
socket.send_string("[OK] Mensaje enviado")
```

**PASO 4: Servidor (PC 1) - Thread ChatBroadcast**
```python
# Está en su bucle esperando
if not self.cola_chat.empty():        # Detecta que hay algo
    mensaje = self.cola_chat.get()    # Saca "[Juan] Hola a todos"
    socket.send_string("CHAT:[Juan] Hola a todos")  # Publica a todos
```

**PASO 5: Cliente de María (PC 3) - Thread ClienteReceptor**
```python
# Está escuchando en socket SUB
mensaje = socket.recv_string()  # "CHAT:[Juan] Hola a todos"
contenido = mensaje[5:]         # "[Juan] Hola a todos"
print(f"\n{contenido}")         # María lo ve en su terminal
print(">> ", end="")            # Re-imprime el prompt
```

**PASO 6: Cliente de Pedro (PC 4) - Thread ClienteReceptor**
```python
# Lo mismo que María
mensaje = socket.recv_string()
print(f"\n[Juan] Hola a todos")  # Pedro también lo ve
```

---

## **PARTE 5: GESTIÓN DE THREADS Y COLAS**

### **5.1 ¿Cómo se crean y arrancan?**

```python
# En modo_servidor()
cola_chat = Queue()  # Crear la cola

servidor = Servidor(cola_chat)      # Crear thread (no inicia aún)
chat_broadcast = ChatBroadcast(cola_chat)

servidor.start()         # AQUÍ inicia el thread (llama a run())
chat_broadcast.start()   # AQUÍ inicia el otro thread
```

**Importante:** 
- `Servidor(cola_chat)` solo crea el objeto
- `.start()` inicia el thread y ejecuta `run()`

### **5.2 ¿Cómo se detienen de forma segura?**

```python
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:  # Usuario presiona Ctrl+C
    servidor.detener()          # Cambia self.activo = False
    chat_broadcast.detener()
    time.sleep(1)               # Espera a que terminen sus bucles
```

**En cada thread:**
```python
def run(self):
    while self.activo:  # <-- Cuando detener() cambia esto a False
        # ... hacer cosas
    
    socket.close()  # Limpia recursos
    context.term()
```

### **5.3 ¿Por qué daemon = True?**

```python
self.daemon = True
```

Sin esto, si el programa principal termina pero un thread sigue ejecutándose, el programa NO se cierra (queda zombie).

Con `daemon=True`, cuando el programa principal termina, los threads daemon también terminan automáticamente.

---

## **PARTE 6: SINCRONIZACIÓN CON LOCKS**

### **6.1 ¿Qué es self.lock?**

```python
self.lock = threading.Lock()
```

Es un "candado" que asegura que solo un thread acceda a un recurso compartido a la vez.

### **6.2 ¿Cuándo usarlo?**

```python
# INCORRECTO (puede fallar)
def comando_login(self, identidad, nombre):
    self.clientes_conectados[identidad] = nombre  # ¡Peligro!

# CORRECTO
def comando_login(self, identidad, nombre):
    with self.lock:  # Adquiere el candado
        self.clientes_conectados[identidad] = nombre
    # Al salir del with, suelta el candado automáticamente
```

**¿Por qué es necesario?**

Imagina dos clientes haciend `/login` al mismo tiempo:

```
Thread A: lee self.clientes_conectados
Thread B: lee self.clientes_conectados (mismo estado)
Thread A: modifica y guarda
Thread B: modifica y guarda (sobreescribe cambios de A)
```

Con lock:
```
Thread A: adquiere lock
Thread A: modifica self.clientes_conectados
Thread A: suelta lock
Thread B: adquiere lock (esperó a que A terminara)
Thread B: modifica self.clientes_conectados
Thread B: suelta lock
```

**IMPORTANTE:** Las colas (`Queue`) ya tienen locks internos, por eso no necesitas protegerlas manualmente.

---

## **PARTE 7: CONEXIÓN ENTRE COMPUTADORAS**

### **7.1 En el servidor:**

```python
socket.bind(f"tcp://*:{SERVIDOR_PORT}")
```

`*` o `0.0.0.0` significa "escuchar en TODAS las interfaces de red":
- Localhost (127.0.0.1)
- WiFi (192.168.1.100)
- Ethernet (192.168.0.50)

### **7.2 En el cliente:**

```python
ip_servidor = input("IP del servidor: ")
socket.connect(f"tcp://{ip_servidor}:{SERVIDOR_PORT}")
```

Si ambos están en la misma red, el cliente usa la IP local del servidor (ejemplo: 192.168.1.100).

### **7.3 ¿Qué pasa detrás de escenas?**

1. Servidor hace `bind()` → Sistema operativo abre el puerto 5555
2. Cliente hace `connect()` → Envía paquete TCP al servidor
3. Servidor acepta conexión → Se establece canal bidireccional
4. Ambos pueden enviar/recibir datos por ese canal

---

## **RESUMEN FINAL: ¿Cómo todo se conecta?**

```
1. Creas una COLA (Queue) para comunicar threads
2. Creas THREADS pasándoles referencias a las colas
3. Llamas a .start() para que ejecuten sus run()
4. Cada thread en su run():
   - Crea sockets ZMQ
   - Entra en bucle while self.activo
   - Lee de colas / envía a colas
   - Usa locks para modificar datos compartidos
5. Para detener: cambias self.activo = False
```

**Ventajas de esta arquitectura:**
- **Concurrencia:** Múltiples cosas pasan simultáneamente
- **Desacoplamiento:** Los threads no se conocen entre sí, solo usan colas
- **Escalabilidad:** Puedes agregar más threads fácilmente
- **Distribución:** ZMQ permite comunicación entre computadoras

---

