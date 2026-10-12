import { SettingsService } from './settings.service';

describe('SettingsService', () => {
  it('uses OpenAI, the only provider', () => {
    const service = new SettingsService();
    expect(service.provider()).toBe('openai');
    expect(service.isOpen()).toBe(false);
  });

  it('opens and closes the settings modal', () => {
    const service = new SettingsService();
    service.open();
    expect(service.isOpen()).toBe(true);
    service.close();
    expect(service.isOpen()).toBe(false);
  });
});
