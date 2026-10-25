import { Head } from "$fresh/runtime.ts";
import type { PageProps } from "$fresh/server.ts";

export default function App({ Component, url }: PageProps) {
  const path = url.pathname;
  const link = (href: string, label: string) => (
    <a class={path === href ? "nav-link active" : "nav-link"} href={href}>
      {label}
    </a>
  );
  return (
    <html lang="en">
      <Head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>ParcelDesk · Autonomous Operations</title>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
        <link
          href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Space+Grotesk:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
        <link rel="stylesheet" href="/app.css" />
      </Head>
      <body>
        <header class="topbar">
          <a class="brand" href="/">
            <span class="brand-mark">Æ</span>
            <span>
              <strong>ParcelDesk</strong>
              <small>autonomous operations</small>
            </span>
          </a>
          <nav>
            {link("/", "Warehouse")}
            {link("/tickets", "Factory tickets")}
            {link("/pulls", "Review queue")}
          </nav>
          <div class="topbar-status">
            <span class="pulse" />
            <span>factory online</span>
          </div>
        </header>
        <main>
          <Component />
        </main>
        <footer class="footer">
          <span>PARCELDESK / LOCAL DEMO</span>
          <span>Quarkus + Hibernate · Deno Fresh · Three.js</span>
          <span>No customer data is real.</span>
        </footer>
        <div id="toast" class="toast" role="status" aria-live="polite" />
        <script src="/app.js" type="module" defer />
      </body>
    </html>
  );
}
