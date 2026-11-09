import { spawnSync } from "node:child_process";

import { cac } from "cac";

const cli = cac("agentforge");

cli.command("doctor", "Check local development prerequisites").action(() => {
  const checks = [
    checkCommand("node", ["--version"]),
    checkCommand("pnpm", ["--version"]),
    checkCommand("git", ["--version"]),
    checkCommand("docker", ["info", "--format", "{{.ServerVersion}}"]),
  ];

  console.table(checks);
  if (checks.some((check) => !check.ready)) {
    process.exitCode = 1;
  }
});

cli.help();
cli.parse();

function checkCommand(command: string, args: string[]) {
  const useCommandShell = process.platform === "win32" && command === "pnpm";
  const executable = useCommandShell ? process.env.ComSpec ?? "cmd.exe" : command;
  const commandArgs = useCommandShell ? ["/d", "/s", "/c", `pnpm ${args.join(" ")}`] : args;
  const result = spawnSync(executable, commandArgs, { encoding: "utf8" });
  const output = result.stdout || result.stderr || result.error?.message || "no output";
  return {
    command,
    ready: result.status === 0,
    version: output.trim().split(/\r?\n/, 1)[0] ?? "",
  };
}
