// src/chart/drawings/CommandStack.ts
import type { Drawing, WsAction } from '../../types';

export type CommandAction =
  | { type: 'ADD'; drawing: Drawing }
  | { type: 'DELETE'; drawing: Drawing }
  | { type: 'UPDATE'; prev: Drawing; next: Drawing };

export class CommandStack {
  private stack: CommandAction[] = [];
  private pointer = -1;

  private readonly sendAction: (action: WsAction) => void;
  private readonly applyLocally: (cmd: CommandAction, isUndo: boolean) => void;

  constructor(
    sendAction: (action: WsAction) => void,
    applyLocally: (cmd: CommandAction, isUndo: boolean) => void,
  ) {
    this.sendAction = sendAction;
    this.applyLocally = applyLocally;
  }

  push(cmd: CommandAction): void {
    // Обрізаємо “майбутнє”, якщо після undo почали нову гілку
    this.stack = this.stack.slice(0, this.pointer + 1);
    this.stack.push(cmd);
    this.pointer++;

    // ✅ ЧЕСНО: застосовуємо одразу локально (без очікування ack)
    this.applyLocally(cmd, false);

    // Дзеркально відправляємо на бекенд
    this.sendForward(cmd);
  }

  undo(): void {
    if (this.pointer < 0) return;

    const cmd = this.stack[this.pointer];

    // Локальний відкат
    this.applyLocally(cmd, true);

    // Відкат на сервері
    this.sendInverse(cmd);

    this.pointer--;
  }

  redo(): void {
    if (this.pointer >= this.stack.length - 1) return;

    this.pointer++;
    const cmd = this.stack[this.pointer];

    // Локально повтор
    this.applyLocally(cmd, false);

    // Повтор на сервері
    this.sendForward(cmd);
  }

  private sendForward(cmd: CommandAction): void {
    if (cmd.type === 'ADD') {
      this.sendAction({ action: 'drawing_add', drawing: cmd.drawing });
      return;
    }
    if (cmd.type === 'DELETE') {
      this.sendAction({ action: 'drawing_remove', id: cmd.drawing.id });
      return;
    }
    // UPDATE
    this.sendAction({ action: 'drawing_update', drawing: cmd.next });
  }

  private sendInverse(cmd: CommandAction): void {
    if (cmd.type === 'ADD') {
      this.sendAction({ action: 'drawing_remove', id: cmd.drawing.id });
      return;
    }
    if (cmd.type === 'DELETE') {
      this.sendAction({ action: 'drawing_add', drawing: cmd.drawing });
      return;
    }
    // UPDATE (inverse = prev)
    this.sendAction({ action: 'drawing_update', drawing: cmd.prev });
  }
}