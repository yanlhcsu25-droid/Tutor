"""Identity boundary for the current single-user Teacher Agent application.

The project has no authenticated teacher identity model yet. Keep that
limitation explicit and centralized instead of scattering magic owner strings.
A future auth layer should inject a stable owner key into ``run_teacher_agent``.
"""

DEFAULT_TEACHER_OWNER_KEY = "local_teacher"
