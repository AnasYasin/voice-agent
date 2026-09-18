# The agent and the demo server, in one image.
#
# ffmpeg is the reason this is not just a pip install. audio.to_telephone
# shells out to it, and the greeting goes through that path on every single
# call, so a box without ffmpeg fails on the first thing the agent says.
FROM python:3.11-slim

# libgomp1 is for the Silero VAD wheel, which is native and links against it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first, so a code change does not reinstall the world.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config/ ./config/
COPY web/ ./web/

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

# Fail the build here rather than on the first caller. The model ships inside
# the wheel, so this loads in milliseconds and is checking the native library
# is linked, not warming a download.
RUN python -c "from livekit.plugins import silero; silero.VAD.load()"

EXPOSE 8080

CMD ["python", "-m", "voice_agent.main", "--serve"]
