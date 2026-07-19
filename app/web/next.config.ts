import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // better-sqlite3 is a native addon: it must stay an external CommonJS require
  // on the server rather than being traced/bundled by webpack or turbopack.
  serverExternalPackages: ["better-sqlite3"],
  // Pin the workspace root; without it Turbopack walks up past app/web looking
  // for a lockfile and warns about ambiguity.
  turbopack: { root: __dirname },
};

export default nextConfig;
