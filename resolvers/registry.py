from .hubcloud import HubCloudResolver
from .direct import DirectResolver

# Put specific resolvers before the generic DirectResolver.
RESOLVERS = [
    HubCloudResolver(),
    DirectResolver(),
]


def get_resolver(url: str):
    for resolver in RESOLVERS:
        if resolver.can_handle(url):
            return resolver
    return None
