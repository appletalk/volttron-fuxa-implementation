# VOLTTRON 10 (modular) platform: platform-driver + Modbus interface +
# sqlite historian, plus (Phase 4) the fuxa-bridge agent.
#
# Python is pinned to 3.10 because the modular VOLTTRON stack targets >=3.10,<4
# and several deps (gevent, etc.) only have prebuilt wheels there.
FROM python:3.10-bookworm

# VOLTTRON refuses to run as root, so run the platform as an unprivileged user.
RUN useradd --create-home --shell /bin/bash volttron
ENV VOLTTRON_HOME=/home/volttron/.volttron
ENV PATH=/home/volttron/venv/bin:$PATH

USER volttron
WORKDIR /home/volttron

# Dedicated venv owned by the volttron user.
# Pre-create VOLTTRON_HOME (and the historian data dir) owned by volttron, so a
# volume mounted at .../data doesn't leave the parent root-owned.
RUN mkdir -p /home/volttron/.volttron/data

RUN python -m venv /home/volttron/venv && pip install --no-cache-dir -U pip

# Install the platform + agents. --pre because the whole ecosystem is RC/alpha.
COPY --chown=volttron:volttron requirements.txt .
RUN pip install --no-cache-dir --pre -r requirements.txt

# Config store payloads + the fuxa-bridge agent + startup orchestration.
COPY --chown=volttron:volttron config/ /home/volttron/config/
COPY --chown=volttron:volttron bridge/ /home/volttron/bridge/
COPY --chown=volttron:volttron entrypoint.sh /home/volttron/entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["/home/volttron/entrypoint.sh"]
