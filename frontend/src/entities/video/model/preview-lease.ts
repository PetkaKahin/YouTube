// Media instances stay outside serializable UI/server state.
let stopCurrent: (() => void) | undefined;
export function claimVideoPreview(stop: () => void) {
  stopCurrent?.();
  stopCurrent = stop;
  return () => {
    if (stopCurrent === stop) stopCurrent = undefined;
  };
}
