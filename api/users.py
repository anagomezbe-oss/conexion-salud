"""Admin-only user creation through Supabase Auth Admin API.

The service-role key is read only by this Vercel function and is never returned
to the browser. Existing user profile changes remain protected by RLS.
"""
from http.server import BaseHTTPRequestHandler
from urllib.error import HTTPError, URLError
from urllib.parse import quote
import json
import os
import urllib.request


def _request(url, headers, method="GET", body=None):
    payload = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=payload, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw)
        except Exception:
            detail = {}
        raise RuntimeError(detail.get("msg") or detail.get("message") or "Supabase rechazó la solicitud.") from exc
    except URLError as exc:
        raise RuntimeError("No fue posible conectar con Supabase.") from exc


class handler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            url = os.environ.get("SUPABASE_URL", "").rstrip("/")
            public_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "")
            service_key = os.environ.get("SUPABASE_SECRET_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""))
            if not (url and public_key and service_key):
                return self.send_json(503, {"error": "Falta configurar Supabase en Vercel."})
            admin_headers = {"apikey": service_key, "Authorization": "Bearer " + service_key}
            auth = self.headers.get("Authorization", "")
            bootstrap_secret = os.environ.get("BOOTSTRAP_ADMIN_SECRET", "")
            is_bootstrap = bool(bootstrap_secret and self.headers.get("X-Bootstrap-Secret", "") == bootstrap_secret)
            if is_bootstrap:
                _, profiles = _request(url + "/rest/v1/profiles?select=id&limit=1", admin_headers)
                if profiles:
                    return self.send_json(409, {"error": "El acceso inicial ya fue configurado."})
            else:
                if not auth.startswith("Bearer "):
                    return self.send_json(401, {"error": "Inicia sesión para continuar."})
                token = auth[7:]
                _, actor = _request(url + "/auth/v1/user", {"apikey": public_key, "Authorization": "Bearer " + token})
                actor_id = actor.get("id")
                if not actor_id:
                    return self.send_json(401, {"error": "La sesión no es válida."})
                _, profiles = _request(url + "/rest/v1/profiles?id=eq." + quote(actor_id) + "&select=role,active", admin_headers)
                if not profiles or profiles[0].get("role") != "admin" or not profiles[0].get("active"):
                    return self.send_json(403, {"error": "Solo administración puede crear usuarios."})
            length = min(int(self.headers.get("Content-Length", "0")), 16384)
            payload = json.loads(self.rfile.read(length) or b"{}")
            name = str(payload.get("name", "")).strip()
            email = str(payload.get("email", "")).strip().lower()
            password = str(payload.get("password", ""))
            role = payload.get("role")
            entity = str(payload.get("entity", "")).strip()
            if is_bootstrap:
                role, entity = "admin", "org-admin"
            if not name or "@" not in email or len(password) < 8 or role not in ("punto_salud", "proveedor", "eps", "admin"):
                return self.send_json(400, {"error": "Revisa nombre, correo, perfil y contraseña (mínimo 8 caracteres)."})
            _, organizations = _request(url + "/rest/v1/organizations?select=id,name&active=eq.true", admin_headers)
            match = next((org for org in organizations if org["id"] == entity or org["name"].casefold() == entity.casefold()), None)
            if not match:
                return self.send_json(400, {"error": "La entidad indicada no existe."})
            _, created = _request(url + "/auth/v1/admin/users", {**admin_headers, "Content-Type": "application/json"}, "POST", {
                "email": email,
                "password": password,
                "email_confirm": True,
                "user_metadata": {"name": name},
            })
            if not created.get("id"):
                raise RuntimeError("Supabase no devolvió el identificador de la cuenta creada.")
            # Assign trusted app metadata after Auth has inserted the user.
            # The database trigger then creates the matching public profile.
            _request(url + "/auth/v1/admin/users/" + quote(created["id"]), {**admin_headers, "Content-Type": "application/json"}, "PUT", {
                "app_metadata": {"role": role, "entity_id": match["id"]},
            })
            return self.send_json(201, {"id": created.get("id"), "email": email})
        except RuntimeError as exc:
            return self.send_json(400, {"error": str(exc)})
        except Exception:
            return self.send_json(400, {"error": "No se pudo crear el usuario. Revisa los datos."})

    def log_message(self, *_args):
        return
