"""
VY-WEB Cloud Desktop Automation
================================
SaaS de Automacao de Desktop na Nuvem

Integra Orgo AI (infraestrutura de desktop na nuvem) com Agent-S (framework
de agente de IA para GUI). Quando Agent-S nao esta disponivel ou a maquina
nao possui recursos suficientes, utiliza o modo nativo de Computer Use da
Orgo como fallback.

Fluxo End-to-End:
  1. Provisiona VM na Orgo AI
  2. Tenta instalar Agent-S (com self-healing)
  3. Executa missao (navegar ate web.whatsapp.com)
  4. Tira screenshot de verificacao
  5. Baixa screenshot e destroi a VM

Uso:
  export ORGO_API_KEY=sk_live_...
  python vy_cloud.py
"""

import os
import sys
import time
import logging
from pathlib import Path

from dotenv import load_dotenv
from orgo import Computer

load_dotenv()

WORKSPACE_ID = "283e0dd7-a305-4842-9caf-4f6a38492877"
VM_NAME = "agent-s-runner"
VM_RAM = 8
VM_CPU = 4
PROVISION_TIMEOUT = 60
SCREENSHOT_LOCAL_PATH = Path("vy_screenshot_final.png")
MISSION_URL = "https://web.whatsapp.com"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("vy-web")


def _ensure_api_key():
    key = os.environ.get("ORGO_API_KEY", "")
    if not key:
        log.error("ORGO_API_KEY nao definida. Configure a variavel de ambiente.")
        sys.exit(1)
    log.info("ORGO_API_KEY detectada.")
    return key


def provision_vm():
    log.info("Criando maquina virtual na Orgo AI...")
    log.info(
        "  Workspace: %s | Nome: %s | RAM: %dGB | CPU: %d cores",
        WORKSPACE_ID,
        VM_NAME,
        VM_RAM,
        VM_CPU,
    )

    computer = Computer(
        workspace=WORKSPACE_ID,
        name=VM_NAME,
        ram=VM_RAM,
        cpu=VM_CPU,
    )

    log.info("Aguardando VM ficar pronta (timeout: %ds)...", PROVISION_TIMEOUT)
    start = time.time()
    while True:
        elapsed = time.time() - start
        if elapsed > PROVISION_TIMEOUT:
            log.error("Timeout: VM nao ficou pronta em %ds. Abortando.", PROVISION_TIMEOUT)
            raise TimeoutError(f"VM nao ficou pronta em {PROVISION_TIMEOUT}s")

        try:
            status_info = computer.status()
            status = status_info.get("status", "unknown") if isinstance(status_info, dict) else str(status_info)
            log.info("  Status da VM: %s (%.0fs)", status, elapsed)
            if status in ("running", "active", "ready"):
                break
        except Exception as exc:
            log.warning("  Erro ao verificar status: %s (%.0fs)", exc, elapsed)

        time.sleep(5)

    log.info("Maquina criada e pronta!")
    return computer


def _install_system_deps(computer):
    log.info("Instalando dependencias do sistema (tesseract-ocr, etc.)...")
    deps_cmd = (
        "sudo apt-get update -qq && "
        "sudo apt-get install -y -qq tesseract-ocr python3-venv python3-pip "
        "libx11-dev libxtst-dev libpng-dev firefox-esr 2>&1"
    )
    try:
        output = computer.bash(deps_cmd)
        log.info("  Dependencias do sistema instaladas. Output (ultimas 200 chars): ...%s", str(output)[-200:])
        return True
    except Exception as exc:
        log.warning("  Falha ao instalar deps do sistema: %s", exc)
        return False


def _install_agent_s_venv(computer):
    log.info("Criando venv e instalando Agent-S (gui-agents)...")
    venv_cmds = [
        "python3 -m venv /home/user/vy_venv",
        "/home/user/vy_venv/bin/pip install --upgrade pip",
        "/home/user/vy_venv/bin/pip install gui-agents",
    ]
    for cmd in venv_cmds:
        log.info("  Executando: %s", cmd)
        try:
            output = computer.bash(cmd)
            log.info("  OK: ...%s", str(output)[-150:])
        except Exception as exc:
            log.warning("  Falha: %s", exc)
            return False
    return True


def try_install_agent_s(computer, max_retries=2):
    for attempt in range(1, max_retries + 1):
        log.info("=== Tentativa %d/%d de instalar Agent-S ===", attempt, max_retries)

        deps_ok = _install_system_deps(computer)
        if not deps_ok and attempt < max_retries:
            log.info("  Self-healing: tentando reinstalar dependencias do sistema...")
            computer.bash("sudo apt-get install -f -y 2>&1 || true")
            continue

        venv_ok = _install_agent_s_venv(computer)
        if venv_ok:
            log.info("Agent-S instalado com sucesso na VM!")
            return True

        log.warning("Instalacao do Agent-S falhou na tentativa %d.", attempt)

    log.warning("Nao foi possivel instalar Agent-S apos %d tentativas.", max_retries)
    return False


def run_mission_agent_s(computer):
    log.info("Executando missao via Agent-S...")
    agent_script = f"""
import sys
sys.path.insert(0, '/home/user/vy_venv/lib/python3.*/site-packages')

try:
    import pyautogui
    import subprocess
    import time

    subprocess.Popen(['firefox', '--no-remote', '{MISSION_URL}'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(10)

    screenshot = pyautogui.screenshot()
    screenshot.save('/home/user/vy_mission_screenshot.png')
    print('MISSION_SUCCESS: screenshot salvo em /home/user/vy_mission_screenshot.png')
except Exception as e:
    print(f'MISSION_FAILED: {{e}}')
"""
    try:
        result = computer.exec(agent_script)
        result_str = str(result)
        log.info("  Resultado Agent-S: %s", result_str[:300])
        if "MISSION_SUCCESS" in result_str:
            return True
    except Exception as exc:
        log.warning("  Erro ao executar Agent-S: %s", exc)

    return False


def run_mission_native(computer):
    log.info("Executando missao via Orgo Computer Use nativo (fallback)...")
    log.info("  Instrucao: Abrir navegador e navegar ate %s", MISSION_URL)

    try:
        def on_progress(event_type, event_data):
            if event_type == "text":
                log.info("  [Orgo AI] %s", str(event_data)[:200])
            elif event_type == "tool_use":
                action = event_data.get("action", "unknown") if isinstance(event_data, dict) else str(event_data)
                log.info("  [Orgo AI] Acao: %s", str(action)[:200])

        computer.prompt(
            f"Open Firefox browser and navigate to {MISSION_URL}. "
            "Wait for the page to fully load. Do not close the browser.",
            callback=on_progress,
            verbose=True,
        )

        log.info("  Aguardando pagina carregar (10s)...")
        computer.wait(10.0)
        log.info("Missao executada via Orgo Computer Use nativo.")
        return True
    except Exception as exc:
        log.error("  Falha no fallback nativo: %s", exc)
        return False


def take_and_download_screenshot(computer):
    log.info("Tirando screenshot final...")
    try:
        screenshot = computer.screenshot()
        screenshot.save(str(SCREENSHOT_LOCAL_PATH))
        log.info("Screenshot salvo localmente em: %s", SCREENSHOT_LOCAL_PATH.resolve())
        return True
    except Exception as exc:
        log.warning("Falha ao tirar screenshot via SDK: %s", exc)

    log.info("Tentando screenshot via base64...")
    try:
        import base64
        b64_data = computer.screenshot_base64()
        img_bytes = base64.b64decode(b64_data)
        SCREENSHOT_LOCAL_PATH.write_bytes(img_bytes)
        log.info("Screenshot (base64) salvo em: %s", SCREENSHOT_LOCAL_PATH.resolve())
        return True
    except Exception as exc:
        log.error("Falha ao obter screenshot: %s", exc)
        return False


def cleanup(computer):
    log.info("Destruindo maquina virtual para evitar custos extras...")
    try:
        computer.destroy()
        log.info("Maquina destruida com sucesso.")
    except Exception as exc:
        log.error("ERRO ao destruir maquina: %s", exc)
        log.error("ATENCAO: Verifique manualmente em orgo.ai/workspaces!")


def main():
    log.info("=" * 60)
    log.info("VY-WEB Cloud Desktop Automation - Iniciando")
    log.info("=" * 60)

    _ensure_api_key()
    computer = None

    try:
        computer = provision_vm()

        agent_s_installed = try_install_agent_s(computer)

        mission_ok = False
        if agent_s_installed:
            mission_ok = run_mission_agent_s(computer)

        if not mission_ok:
            log.info("Ativando fallback: Orgo Computer Use nativo...")
            mission_ok = run_mission_native(computer)

        if mission_ok:
            take_and_download_screenshot(computer)
        else:
            log.error("Missao falhou em todos os metodos.")

        log.info("=" * 60)
        if mission_ok:
            log.info("RESULTADO: Missao concluida com sucesso!")
            if SCREENSHOT_LOCAL_PATH.exists():
                log.info("Screenshot disponivel em: %s", SCREENSHOT_LOCAL_PATH.resolve())
        else:
            log.info("RESULTADO: Missao falhou.")
        log.info("=" * 60)

    except TimeoutError:
        log.error("Abortando: VM nao ficou pronta no tempo limite.")
    except KeyboardInterrupt:
        log.warning("Execucao interrompida pelo usuario.")
    except Exception as exc:
        log.error("Erro inesperado: %s", exc, exc_info=True)
    finally:
        if computer is not None:
            cleanup(computer)

    log.info("VY-WEB finalizado.")


if __name__ == "__main__":
    main()
