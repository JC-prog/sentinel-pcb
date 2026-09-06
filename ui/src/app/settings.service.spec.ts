import { SettingsService } from './settings.service';

describe('SettingsService', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('defaults to the ollama provider', () => {
    const service = new SettingsService();
    expect(service.provider()).toBe('ollama');
    expect(service.isOpen()).toBe(false);
  });

  it('opens and closes the settings modal', () => {
    const service = new SettingsService();
    service.open();
    expect(service.isOpen()).toBe(true);
    service.close();
    expect(service.isOpen()).toBe(false);
  });

  it('persists the provider choice across instances', () => {
    const service = new SettingsService();
    service.setProvider('openai');

    const restored = new SettingsService();
    expect(restored.provider()).toBe('openai');
  });
});
