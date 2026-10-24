FROM node:24-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea
WORKDIR /opt/opencode
COPY infra/opencode/package.json infra/opencode/package-lock.json ./
RUN npm ci --ignore-scripts --omit=dev && \
    node -e 'const fs=require("fs"),path=require("path");const pkg=require.resolve(`opencode-linux-${process.arch}/package.json`);fs.copyFileSync(path.join(path.dirname(pkg),"bin/opencode"),"/usr/local/bin/opencode");fs.chmodSync("/usr/local/bin/opencode",0o755)' && \
    opencode --version && npm cache clean --force && rm -rf /opt/opencode
COPY infra/opencode/catalog.json /opt/aeion/catalog.json
ENV HOME=/tmp/home XDG_DATA_HOME=/tmp/data XDG_CONFIG_HOME=/tmp/config XDG_STATE_HOME=/tmp/state XDG_CACHE_HOME=/tmp/cache \
    OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_MODELS_PATH=/opt/aeion/catalog.json
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["opencode"]
