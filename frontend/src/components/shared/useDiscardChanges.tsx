import { useEffect, useRef, useState } from "react";

export function useDiscardChanges() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [pending, setPending] = useState<(() => void) | null>(null);
  useEffect(() => {
    if (pending) dialog.current?.showModal();
    else dialog.current?.close();
  }, [pending]);
  return {
    confirmDiscard: (action: () => void) => setPending(() => action),
    discardDialog: <dialog ref={dialog} className="discard-dialog" aria-label="未保存的修改" onCancel={event => { event.preventDefault(); setPending(null); }}>
      <h2>还有未保存的修改</h2>
      <p>继续离开会放弃当前修改。已保存的备课和作业不受影响。</p>
      <div className="training-actions">
        <button type="button" className="ghost-action" autoFocus onClick={() => setPending(null)}>继续编辑</button>
        <button type="button" className="ghost-action danger" onClick={() => { const action = pending; setPending(null); action?.(); }}>放弃修改并继续</button>
      </div>
    </dialog>,
  };
}
