from django.contrib.auth.hashers import PBKDF2PasswordHasher


class FastPBKDF2PasswordHasher(PBKDF2PasswordHasher):
    """Same algorithm as Django's default (pbkdf2_sha256) with fewer iterations, so signing in is ~4x faster.
    Django 5.2's default is 1,000,000, which takes ~0.6 s per sign-in on a normal CPU and longer inside Docker.
    260,000 is what Django used as its default for years. Old hashes keep working and are upgraded on the next sign-in."""
    iterations = 260_000
