/**
 * VS Code half of the release-canary-vscode-ext synthetic extension.
 *
 * One command, wired to the pure `report` helper — just enough surface
 * for `vsce package` to produce a real VSIX. See
 * arthur-debert/release#587.
 */

import * as vscode from "vscode";

import { report } from "./liveness";

export function activate(context: vscode.ExtensionContext): void {
  context.subscriptions.push(
    vscode.commands.registerCommand("canary.report", () => {
      void vscode.window.showInformationMessage(report("canary"));
    }),
  );
}

export function deactivate(): void {
  // Nothing to dispose beyond the subscriptions VS Code already tracks.
}
