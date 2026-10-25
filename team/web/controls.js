import {setPaperFraud} from './api.js';
import {isFresh, fraudDirection} from './telemetry.js';

export class FraudControl {
  constructor({input, reset, label, message, mode, send = setPaperFraud}) {
    Object.assign(this, {input, reset, label, message, mode, send});
    this.pending = false;
    this.latest = null;
    input.addEventListener('change', () => this.command(input.checked ? Number(this.mode?.value || 1) : 0));
    mode?.addEventListener('change', () => {
      if (this.input.checked) this.command(Number(mode.value));
    });
    reset.addEventListener('click', () => this.command(0));
  }
  render(data) {
    this.latest = data;
    const confirmed = data.fraud ? fraudDirection(data) : 0;
    if (this.pending && confirmed === this.desired) {
      this.pending = false;
      this.setMessage('Изменение подтверждено телеметрией и записано в журнал.');
    } else if (this.pending && Date.now() > this.deadline) {
      this.pending = false;
      this.setMessage('Подтверждение пока не получено. Проверьте связь со стендом.', true);
    }
    this.input.disabled = this.reset.disabled = this.pending || !isFresh(data);
    if (this.mode) {
      this.mode.disabled = this.pending || !isFresh(data);
      if (!this.pending && data.fraud) this.mode.value = String(confirmed);
    }
    if (!this.pending) this.input.checked = !!data.fraud;
    this.label.textContent = this.pending ? 'ОЖИДАНИЕ' : data.fraud ? 'ON' : 'OFF';
  }
  setMessage(text, error = false) {
    this.message.textContent = text;
    this.message.classList.toggle('error', error);
  }
  unavailable(text) {
    this.input.disabled = this.reset.disabled = true;
    if (this.mode) this.mode.disabled = true;
    this.setMessage(text, true);
  }
  async command(value) {
    if (this.pending) return;
    this.pending = true;
    this.desired = value;
    this.deadline = Date.now() + 15000;
    this.input.disabled = this.reset.disabled = true;
    if (this.mode) this.mode.disabled = true;
    this.label.textContent = 'ОЖИДАНИЕ';
    this.setMessage('Передаем изменение в систему…');
    try {
      await this.send(value);
      if (this.pending) this.setMessage('Команда принята. Проверяем новое состояние двойника…');
    } catch (error) {
      this.pending = false;
      this.render(this.latest || {fraud: false, mqtt_connected: false});
      this.setMessage(error.message, true);
    }
  }
}
