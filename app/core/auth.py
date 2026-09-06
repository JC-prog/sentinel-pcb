"""Domain exceptions raised by app/auth/service.py, translated to HTTP responses at the route
layer (app/main.py). Kept out of app/auth/service.py so other layers (e.g. a future app/agents/
tool) can catch them without importing the service module itself.
"""


class EmailAlreadyRegistered(Exception):
    pass


class UsernameAlreadyRegistered(Exception):
    pass


class EmployeeIdAlreadyRegistered(Exception):
    pass


class InvalidCredentials(Exception):
    pass


class InvalidRefreshToken(Exception):
    pass
