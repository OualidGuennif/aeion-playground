FROM denoland/deno:2.5.0
WORKDIR /app
COPY . .
RUN deno cache main.ts \
    && deno eval 'const response = await fetch("https://unpkg.com/three@0.170.0/build/three.module.js"); if (!response.ok) throw new Error(String(response.status)); await Deno.writeTextFile("static/three.module.js", await response.text())'
USER deno
ENV PORT=8000 HOME=/tmp
EXPOSE 8000
CMD ["deno", "run", "-A", "--cached-only", "main.ts"]
