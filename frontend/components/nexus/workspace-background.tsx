/** Identical image/dim layers for the real workspace and the Appearance preview. */
export function WorkspaceBackground() {
  return <div className="fx-workspace-background" aria-hidden="true">
    <div className="fx-workspace-background-image" />
    <div className="fx-workspace-background-dim" />
  </div>;
}
