# P2P Dynamic Port Switching

This project has been reconstructed using Python instead of C to comply with the *SENG 491 - 492 Graduation Project (SDD)* standards. The core mechanism is based on Moving Target Defense (MTD); two nodes periodically switch their communication ports (time-step hopping) and dynamically transition between TCP and UDP based on real-time network stability (Automatic Protocol Decision). Furthermore, they automatically exchange their asymmetric keys (Asymmetric Bootstrap) via RSA encryption.

Additionally, with the embedded **Live Web UI**, you can monitor this terminal-based P2P network architecture second-by-second from a modern browser window!

## Requirements and Installation

To run the system smoothly, **only 1** external library is required. Apart from Python's standard libraries, the only dependency is the `cryptography` module. The web server is written entirely using Python's standard library (stdlib) and does not require any external web framework.

### For Windows Users
You can install the library directly via the terminal (CMD or PowerShell) in the project directory using:
```bash
pip install cryptography
```

### For Linux (Kali / Ubuntu) and Mac Users
To avoid PEP-668 system protection issues on modern operating systems, it is recommended to install the cryptography library via your primary package manager:
```bash
sudo apt update
sudo apt install python3-cryptography
```

## Running the Project and Connecting to the Web UI

You can start the system by following the steps below. The moment you enter the commands, the system will start working and **automatically launch a stunning Web UI tab in your browser (e.g., Chrome) at `http://localhost:8080`**.

Assuming our devices are:
* **Device A (Ubuntu - Listener):** Local Network IP address `192.168.1.22`
* **Device B (Kali - Dialer):** Local Network IP address `192.168.1.21`

### Step 1: Device A (Node 0 - Listener Leader)
Open the Ubuntu terminal and start the main program by specifying **Device B's (Kali) address as the target IP**:
```bash
python main.py --peer-id 0 --target-ip 192.168.1.21
```

### Step 2: Device B (Node 1 - Dialer)
Open the Kali terminal and connect by specifying **Device A's (Ubuntu) address as the target IP**:
```bash
python main.py --peer-id 1 --target-ip 192.168.1.22
```

The moment the system is executed, a Web UI server boots up behind the terminal. A browser window will pop up, and you will be able to monitor the P2P hops live via `http://localhost:8080`.

## Advanced Startup Parameters (All Options)

The project is not limited to just `--peer-id` and `--target-ip`. If you wish to test the system in different ways during your jury presentation, you can use the following parameters:

| Parameter | Function | Default Value | Example Usage |
|---|---|---|---|
| `--peer-id` | Device identity (0 for Listener, 1 for Dialer). Required. | - | `--peer-id 1` |
| `--target-ip` | The IP address of the target computer. | `127.0.0.1` | `--target-ip 192.168.1.22` |
| `--mode` | Forces the network protocol (`AUTO`, `TCP`, `UDP`). | `AUTO` | `--mode UDP` |
| `--interval` | Port switching interval (in seconds). | `10` | `--interval 5` |
| `--min-port` | The lower bound of the port hopping range. | `20000` | `--min-port 40000` |
| `--max-port` | The upper bound of the port hopping range. | `30000` | `--max-port 50000` |
| `--web-port` | The port for the Web UI server to run on. | `8080` | `--web-port 8081` |

**Note:** It is sufficient for only the Dialer (`--peer-id 1`) to enter these rules. The Dialer encrypts and transmits them to the Listener during the Asymmetric Bootstrap phase, and the Listener automatically obeys the rules.

## Optional: Single Device Test Mode (Test Launcher)
When you do not want to deal with manual setups during development, you can use the **`test_launcher.py`** script. This tool opens two terminals side-by-side on a single computer. To prevent port conflicts, it starts the interface for one on `http://localhost:8080` and the other on `http://localhost:8081`.
```bash
python test_launcher.py
```

## Network Traffic Tracker (Wireshark P2P Tracker)
If you wish to prove the background port traffic using Wireshark, you can run our dedicated tracker tool, which scans the `session.log` file and generates a custom P2P display filter for Wireshark:
```bash
python wireshark_tracker.py
```
