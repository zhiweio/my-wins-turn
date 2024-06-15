import json
import os
import textwrap
from functools import partial
from pathlib import Path

import logging

import paramiko

from wakeonlan import send_magic_packet
import streamlit as st

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

LOG = logging.getLogger(__name__)

# TODO: ugly sleep, replace with proper implementation
sleep_script_name = "my-wins-turn-sleep.ps1"
sleep_script = Path("./" + sleep_script_name)
if not sleep_script.exists():
    sleep_script.write_text(
        textwrap.dedent(
            """
            Add-Type -TypeDefinition @"
            using System;
            using System.Runtime.InteropServices;
            public class SleepHelper {
                [DllImport("Powrprof.dll", SetLastError = true)]
                public static extern bool SetSuspendState(bool hibernate, bool forceCritical, bool disableWakeEvent);
            }
            "@
            [SleepHelper]::SetSuspendState($false, $true, $true)
            """
        ),
        encoding="utf-8",
    )


class Computer:
    """
    Windows system commands reference:
        https://github.com/microsoft/PowerToys/blob/main/src/modules/launcher/Plugins/Microsoft.PowerToys.Run.Plugin.System/Components/Commands.cs
    """

    def __init__(self, host, mac, user, password, port=22):
        self.host = host
        self.port = port
        self.mac = mac
        self.user = user
        self.password = password
        self._ssh_client = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def connect(self):
        if self._ssh_client is not None:
            return
        self._ssh_client = paramiko.SSHClient()
        self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._ssh_client.connect(self.host, self.port, self.user, self.password)

    def close(self):
        if self._ssh_client is not None:
            self._ssh_client.close()

    def exec_command(self, command):
        error = ""
        output = ""
        try:
            stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=15)
            output = stdout.read().decode("gbk")
            error = stderr.read().decode("gbk")
        except Exception as e:
            LOG.error(f"Error executing command: {e}")
            error = str(e)
        return output, error

    def is_available(self):
        if self._ssh_client is None:
            self.connect()
        output, error = self.exec_command("echo hello")
        if error == "":
            return True
        LOG.error(f"Error connecting the SSH: {error}")
        return False

    def _file_exists(self, sftp, remote_path):
        try:
            sftp.stat(remote_path)
            return True
        except FileNotFoundError:
            return False

    def _upload_file(self, local_path, remote_path):
        if self._ssh_client is None:
            self.connect()
        filename = os.path.basename(local_path)
        sftp = None
        try:
            sftp = self._ssh_client.open_sftp()
            if not self._file_exists(sftp, remote_path + filename):
                sftp.put(local_path, remote_path + filename)
                LOG.info(f"File {local_path} uploaded to {remote_path}")
            else:
                LOG.info(f"File {filename} already exists at {remote_path}")

        except Exception as e:
            LOG.error(f"Error uploading: {e}")
        finally:
            if sftp:
                sftp.close()

    def create_sleep_script(self):
        local_path = str(sleep_script)
        remote_path = f"C:/Users/{self.user}/"
        self._upload_file(local_path, remote_path)
        return remote_path + sleep_script_name

    def shutdown(self):
        output, error = self.exec_command("shutdown /s /hybrid /t 0")
        LOG.info(f"Shutdown initiated: {output}")
        return error

    def hibernate(self):
        output, error = self.exec_command(
            "rundll32.exe powrprof.dll,SetSuspendState 1,1,1"
        )
        LOG.info(f"Hibernate initiated: {output}")
        return error

    def sleep(self):
        script_path = self.create_sleep_script()
        output, error = self.exec_command(
            f'powershell -ExecutionPolicy Bypass -File "{script_path}"'
        )
        LOG.info(f"Sleep initiated: {output}")
        return error

    def reboot(self):
        output, error = self.exec_command("shutdown /r /t 0")
        LOG.info(f"Reboot initiated: {output}")
        return error

    def wake(self):
        error = ""
        try:
            LOG.info(f"Wake-on-LAN packet are sending to {self.mac}\t{self.host}")
            send_magic_packet(self.mac, ip_address=self.host)
        except Exception as e:
            LOG.error(f"Error sending Wake-on-LAN packet: {e}")
            error = str(e)
        return error

    def lock(self):
        output, error = self.exec_command("rundll32.exe user32.dll,LockWorkStation")
        LOG.info(f"Lock work station initiated: {output}")
        return error


CACHE_PATH = Path(".mwt")
CACHE_PATH.mkdir(parents=True, exist_ok=True)
CACHE_CONFIG = CACHE_PATH / "config.json"

st.session_state.pc_config = dict()
st.session_state.pc_status = dict()
st.session_state.chosen_pc = "Unknown"


def save_config_cache():
    CACHE_CONFIG.write_text(json.dumps(st.session_state.pc_config))


def load_config_cache():
    if CACHE_CONFIG.exists():
        return json.loads(CACHE_CONFIG.read_text())
    return dict()


def persist_pc_credential(credential):
    computer_name = credential["host"]
    if not computer_name:
        return
    if "pc_config" in st.session_state:
        st.session_state.pc_config[computer_name] = credential
    else:
        st.session_state.pc_config = {"computer_name": credential}
    save_config_cache()


def retrieve_pc_credential(computer_name):
    return st.session_state.pc_config.get(computer_name, dict())


def reset_pc_settings():
    st.session_state.pc_config = dict()
    st.session_state.pc_status = dict()
    st.session_state.chosen_pc = "Unknown"
    CACHE_CONFIG.write_text(json.dumps({}), encoding="utf8")


def test_pc_available(computer_name):
    if not retrieve_pc_credential(computer_name):
        st.toast(f":orange-background[No PC {computer_name} settings]", icon="⚠️")
        return

    with st.spinner(f"Checking PC {computer_name} Service..."):
        pc = Computer(**retrieve_pc_credential(computer_name))
        try:
            pc.connect()
        except Exception as e:
            LOG.error(f"Error connecting to PC {pc.host}")
            st.session_state.pc_status[computer_name] = "Unavailable"
        else:
            if pc.is_available():
                st.session_state.pc_status[computer_name] = "Available"
            else:
                st.session_state.pc_status[computer_name] = "Unavailable"
            pc.close()


def click_run_computer(computer_name, action, offline=False):
    if not retrieve_pc_credential(computer_name):
        st.toast(":orange-background[No PC settings]", icon="⚠️")
        return
    pc = Computer(**retrieve_pc_credential(computer_name))
    if offline:
        err = getattr(pc, action)()
    else:
        try:
            pc.connect()
            if pc.is_available():
                err = getattr(pc, action)()
            else:
                err = "PC unavailable"
            pc.close()
        except Exception:
            err = "PC already in sleep or shutdown"
    if err:
        st.toast(f":orange-background[{err}]", icon="⚠️")
    else:
        st.toast(":green-background[Completed!]")


def pc_settings_frag():
    col1, col2 = st.columns(2)
    with col1:
        with st.popover("Add New PC settings", use_container_width=True):
            input_host = st.text_input("PC Host/IP")
            input_mac = st.text_input("PC MAC Address")
            input_user = st.text_input("PC Username")
            input_password = st.text_input("PC Password")
            credential = {
                "host": input_host,
                "mac": input_mac,
                "user": input_user,
                "password": input_password,
            }
            st.button(
                "Save",
                on_click=partial(persist_pc_credential, credential=credential),
            )
            st.session_state.pc_config = load_config_cache()
    with col2:
        st.button(
            "Reset all settings", on_click=reset_pc_settings, use_container_width=True
        )


def pc_choose_frag():
    chosen_pc = st.selectbox(
        "Which PC you want to control?",
        options=list(st.session_state.pc_config.keys()),
        index=None,
        placeholder="Select your PC...",
    )
    st.session_state.chosen_pc = chosen_pc


@st.experimental_fragment
def pc_status_frag():
    chosen_pc = st.session_state.chosen_pc or "Unknown"
    with st.expander(f"Control Your PC :rainbow[_{chosen_pc}_]", expanded=True):
        st.markdown(
            """
            **Status:** _{}_ \n
            **Host:** _{}_ \n
            **MAC:** _{}_ \n
            **Username:** _{}_ \n
            """.format(
                st.session_state.pc_status.get(chosen_pc, "Unknown"),
                retrieve_pc_credential(chosen_pc).get("host", "404"),
                retrieve_pc_credential(chosen_pc).get("mac", "404"),
                retrieve_pc_credential(chosen_pc).get("user", "404"),
            )
        )
        st.button(
            "Test connection",
            on_click=partial(test_pc_available, computer_name=chosen_pc),
        )


@st.experimental_fragment
def turn_on_off_frag():
    chosen_pc = st.session_state.chosen_pc
    col1, col2 = st.columns(2)
    col3, col4 = st.columns(2)
    with col1:
        st.button(
            label="Wake",
            type="secondary",
            on_click=partial(
                click_run_computer, computer_name=chosen_pc, action="wake", offline=True
            ),
            use_container_width=True,
        )
    with col2:
        st.button(
            label="Sleep",
            type="secondary",
            on_click=partial(
                click_run_computer, computer_name=chosen_pc, action="sleep"
            ),
            use_container_width=True,
        )
    with col3:
        st.button(
            label="Hibernate",
            type="secondary",
            on_click=partial(
                click_run_computer, computer_name=chosen_pc, action="hibernate"
            ),
            use_container_width=True,
        )
    with col4:
        st.button(
            label="Shutdown",
            type="primary",
            on_click=partial(
                click_run_computer, computer_name=chosen_pc, action="shutdown"
            ),
            use_container_width=True,
        )


st.text("")
st.header("_My Win's Turn!_ :sunglasses:", divider="rainbow")
st.text("Turn on/off my Windows PC remotely.")
pc_settings_frag()
pc_choose_frag()
pc_status_frag()
turn_on_off_frag()
