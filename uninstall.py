import winreg
for browser in ('Google\\Chrome', 'Microsoft\\Edge'):
    path = f'Software\\{browser}\\NativeMessagingHosts\\local.huntsman.readonly'
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        print('Removed:', browser)
    except FileNotFoundError:
        pass
print('Remove the extension in the browser and close Synapse Web tabs.')
