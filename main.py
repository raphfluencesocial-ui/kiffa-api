"""
╔══════════════════════════════════════════════════════════════╗
║         KIFFA-SCORE — SERVEUR API REST                       ║
║         Module : main.py                                     ║
║         Version : 1.0.0                                      ║
║         Framework : FastAPI                                   ║
║         Architecture : In-Memory | Zéro persistance          ║
╚══════════════════════════════════════════════════════════════╝

Description :
    Point d'entrée principal de l'API Kiffa-Score.
    Reçoit les transactions Mobile Money en JSON,
    exécute l'anti-fraude puis le scoring,
    retourne le score final et purge immédiatement
    toutes les données de la mémoire.

Endpoints :
    POST /api/v1/score     — Calculer un score
    GET  /api/v1/health    — Vérifier l'état de l'API
    GET  /api/v1/version   — Informations version

Sécurité :
    - Aucune base de données
    - Données purgées après chaque requête
    - Aucun log contenant des données personnelles
    - Headers de sécurité sur chaque réponse
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from datetime import datetime
import gc
import uuid
import time

# Imports modules Kiffa
from anti_fraude import executer_anti_fraude
from scoring import calculer_score_final


# ═══════════════════════════════════════════════════════════════
# INITIALISATION DE L'APPLICATION
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="KIFFA-SCORE API",
    description="""
    ## API de Credit Scoring Alternatif pour l'Afrique Subsaharienne
    
    **Kiffa-Score** évalue la solvabilité des commerçants du secteur 
    informel en analysant leur historique de transactions Mobile Money.
    
    ### Architecture de sécurité
    - ✅ Zéro base de données — traitement In-Memory uniquement
    - ✅ Données purgées immédiatement après calcul
    - ✅ Aucun log de données personnelles
    - ✅ Conforme CEMAC / ANRPD Cameroun
    
    ### Opérateurs supportés
    - Orange Money Cameroun
    - MTN MoMo Cameroun
    - Wave (prochainement)
    - Moov Money (prochainement)
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION CORS
# Permet à l'interface Netlify d'appeler l'API
# ═══════════════════════════════════════════════════════════════

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://kiffa-score.netlify.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
        "null"  # Pour les fichiers HTML locaux
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type", "X-Request-ID"],
)


# ═══════════════════════════════════════════════════════════════
# MODÈLES DE DONNÉES (Pydantic)
# Validation stricte des données entrantes
# ═══════════════════════════════════════════════════════════════

class Transaction(BaseModel):
    """
    Modèle d'une transaction Mobile Money.
    Validation automatique par Pydantic.
    """
    transaction_id: str = Field(
        ...,
        description="Identifiant unique de la transaction",
        min_length=1,
        max_length=100
    )
    timestamp: str = Field(
        ...,
        description="Date et heure ISO 8601 (ex: 2026-04-15T10:30:00Z)"
    )
    type: str = Field(
        ...,
        description="Direction : IN (entrée) ou OUT (sortie)"
    )
    amount: int = Field(
        ...,
        description="Montant en FCFA (entier positif)",
        gt=0,
        le=50_000_000
    )
    counterparty_id: str = Field(
        ...,
        description="Numéro de téléphone ou nom du fournisseur",
        min_length=1,
        max_length=200
    )
    balance_after: int = Field(
        ...,
        description="Solde résiduel après transaction (en FCFA)",
        ge=0
    )
    is_utility_bill: bool = Field(
        default=False,
        description="True si paiement Eneo/Camwater/facture fixe"
    )

    @validator('type')
    def valider_type(cls, v):
        if v not in ['IN', 'OUT']:
            raise ValueError("Le type doit être 'IN' ou 'OUT'")
        return v

    @validator('timestamp')
    def valider_timestamp(cls, v):
        try:
            ts_clean = v.replace('Z', '+00:00')
            datetime.fromisoformat(ts_clean)
        except ValueError:
            raise ValueError(f"Timestamp invalide : {v}. Format attendu : ISO 8601")
        return v


class PayloadScoring(BaseModel):
    """
    Payload complet reçu par l'endpoint /api/v1/score
    """
    transactions: List[Transaction] = Field(
        ...,
        description="Liste des transactions Mobile Money",
        min_items=5,
        max_items=10_000
    )
    type_profil: Optional[str] = Field(
        default="COMMERCANT",
        description="Type de profil : COMMERCANT ou PME"
    )
    operateur: Optional[str] = Field(
        default="INCONNU",
        description="Opérateur Mobile Money"
    )

    @validator('type_profil')
    def valider_profil(cls, v):
        if v not in ['COMMERCANT', 'PME']:
            raise ValueError("type_profil doit être 'COMMERCANT' ou 'PME'")
        return v


# ═══════════════════════════════════════════════════════════════
# MIDDLEWARE — HEADERS DE SÉCURITÉ
# Appliqué sur chaque réponse
# ═══════════════════════════════════════════════════════════════

@app.middleware("http")
async def ajouter_headers_securite(request: Request, call_next):
    """Ajoute les headers de sécurité sur chaque réponse."""
    debut = time.time()
    response = await call_next(request)
    duree = round((time.time() - debut) * 1000, 2)

    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Kiffa-Version"] = "1.0.0"
    response.headers["X-Processing-Time-Ms"] = str(duree)

    return response


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 1 — HEALTH CHECK
# GET /api/v1/health
# ═══════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/health",
    tags=["Système"],
    summary="Vérifier l'état de l'API"
)
async def health_check():
    """
    Endpoint de santé — vérifie que l'API est opérationnelle.
    Utilisé par les systèmes de monitoring.
    """
    return {
        "statut": "✅ OPÉRATIONNEL",
        "service": "Kiffa-Score API",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "architecture": "In-Memory | Zéro persistance",
        "conformite": "CEMAC / ANRPD Cameroun"
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 2 — VERSION
# GET /api/v1/version
# ═══════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/version",
    tags=["Système"],
    summary="Informations sur la version"
)
async def version():
    """Retourne les informations de version de l'API."""
    return {
        "nom": "Kiffa-Score API",
        "version": "1.0.0",
        "description": "API de Credit Scoring Alternatif — Afrique Subsaharienne",
        "operateurs_supportes": [
            "Orange Money Cameroun",
            "MTN MoMo Cameroun"
        ],
        "piliers_scoring": [
            "Consistance (Cf) — 40 pts",
            "Fréquence (Fa) — 20 pts",
            "Résilience (Rs) — 20 pts",
            "Moralité (Mf) — 20 pts"
        ],
        "contact": "kiffa-score.netlify.app"
    }


# ═══════════════════════════════════════════════════════════════
# ENDPOINT 3 — CALCUL DU SCORE
# POST /api/v1/score
# C'est l'endpoint principal — le coeur de l'API
# ═══════════════════════════════════════════════════════════════

@app.post(
    "/api/v1/score",
    tags=["Scoring"],
    summary="Calculer le score de crédit Kiffa",
    response_description="Score de crédit complet avec détail des piliers"
)
async def calculer_score(payload: PayloadScoring):
    """
    ## Endpoint principal — Calcul du score Kiffa
    
    ### Processus (In-Memory) :
    1. Validation du payload (Pydantic)
    2. Conversion en dictionnaires Python
    3. Module Anti-Fraude (Vélocité + Concentration)
    4. Moteur de Scoring (4 piliers)
    5. Construction de la réponse
    6. Purge immédiate de toutes les données
    
    ### Sécurité :
    - Aucune donnée n'est persistée
    - Le garbage collector purge la RAM après chaque requête
    - Les numéros de téléphone ne sont jamais loggés
    
    ### Retourne :
    - Score Kiffa sur 100
    - Détail des 4 piliers
    - Rapport anti-fraude
    - Décision et montant recommandé
    """
    
    # Générer un ID de requête anonyme pour le tracking
    request_id = str(uuid.uuid4())[:8].upper()
    print(f"\n[KIFFA-API] Nouvelle requête {request_id} — "
          f"{len(payload.transactions)} transactions — "
          f"Profil: {payload.type_profil}")
    
    try:
        # ── ÉTAPE 1 : Convertir en dictionnaires ──
        transactions_dict = [
            t.dict() for t in payload.transactions
        ]
        
        # ── ÉTAPE 2 : Anti-Fraude ──
        resultat_fraude = executer_anti_fraude(transactions_dict)
        transactions_propres = resultat_fraude['transactions_nettoyees']
        malus = resultat_fraude['malus_concentration']
        
        # Vérification : assez de transactions après nettoyage
        if len(transactions_propres) < 3:
            raise HTTPException(
                status_code=422,
                detail={
                    "erreur": "DONNÉES_INSUFFISANTES",
                    "message": "Moins de 3 transactions valides après nettoyage anti-fraude",
                    "transactions_initiales": len(transactions_dict),
                    "transactions_apres_nettoyage": len(transactions_propres)
                }
            )
        
        # ── ÉTAPE 3 : Calcul du Score ──
        resultat_scoring = calculer_score_final(
            transactions=transactions_propres,
            malus_concentration=malus
        )
        
        # ── ÉTAPE 4 : Construction de la réponse ──
        reponse = {
            "request_id": request_id,
            "timestamp_analyse": datetime.utcnow().isoformat() + "Z",
            "type_profil": payload.type_profil,
            "operateur": payload.operateur,
            
            # Score principal
            "kiffa_score": resultat_scoring['kiffa_score'],
            "mention": resultat_scoring['mention'],
            "decision": resultat_scoring['decision'],
            "montant_max_recommande_fcfa": resultat_scoring['montant_max_recommande_fcfa'],
            "couleur_decision": resultat_scoring['couleur_decision'],
            "resume": resultat_scoring['resume'],
            
            # Détail des piliers
            "piliers": {
                "consistance": {
                    "score": resultat_scoring['piliers']['consistance']['score'],
                    "max": 40,
                    "niveau": resultat_scoring['piliers']['consistance']['niveau'],
                    "detail": resultat_scoring['piliers']['consistance']['detail']
                },
                "frequence": {
                    "score": resultat_scoring['piliers']['frequence']['score'],
                    "max": 20,
                    "niveau": resultat_scoring['piliers']['frequence']['niveau'],
                    "detail": resultat_scoring['piliers']['frequence']['detail']
                },
                "resilience": {
                    "score": resultat_scoring['piliers']['resilience']['score'],
                    "max": 20,
                    "niveau": resultat_scoring['piliers']['resilience']['niveau'],
                    "detail": resultat_scoring['piliers']['resilience']['detail']
                },
                "moralite": {
                    "score": resultat_scoring['piliers']['moralite']['score'],
                    "max": 20,
                    "niveau": resultat_scoring['piliers']['moralite']['niveau'],
                    "detail": resultat_scoring['piliers']['moralite']['detail']
                }
            },
            
            # Rapport anti-fraude
            "anti_fraude": {
                "score_confiance": resultat_fraude['score_confiance_anti_fraude'],
                "statut": resultat_fraude['statut_global'],
                "transactions_analysees": resultat_fraude['nb_transactions_analysees'],
                "transactions_exclues": (
                    resultat_fraude['nb_transactions_initiales'] -
                    resultat_fraude['nb_transactions_analysees']
                ),
                "malus_concentration_applique": malus > 0,
                "paires_transit_detectees": resultat_fraude[
                    'rapport_velocite'
                ].get('paires_transit_detectees', 0)
            },
            
            # Mention légale
            "mention_legale": (
                "Ce score est généré automatiquement à titre indicatif. "
                "La décision finale d'octroi de crédit appartient "
                "exclusivement à l'institution financière partenaire. "
                "Conforme CEMAC / ANRPD Cameroun."
            )
        }
        
        print(f"[KIFFA-API] {request_id} — Score : "
              f"{resultat_scoring['kiffa_score']}/100 — "
              f"{resultat_scoring['decision']}")
        
        # ── ÉTAPE 5 : PURGE IMMÉDIATE DE LA MÉMOIRE ──
        del transactions_dict
        del transactions_propres
        del resultat_fraude
        del resultat_scoring
        del payload
        gc.collect()
        
        print(f"[KIFFA-API] {request_id} — Mémoire purgée ✅")
        
        return JSONResponse(content=reponse, status_code=200)
    
    except HTTPException:
        raise
    
    except Exception as e:
        print(f"[KIFFA-API] {request_id} — ERREUR : {str(e)}")
        gc.collect()
        raise HTTPException(
            status_code=500,
            detail={
                "erreur": "ERREUR_INTERNE",
                "message": "Une erreur est survenue lors du calcul",
                "request_id": request_id
            }
        )


# ═══════════════════════════════════════════════════════════════
# GESTIONNAIRE D'ERREURS GLOBAL
# ═══════════════════════════════════════════════════════════════

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "erreur": "ENDPOINT_INTROUVABLE",
            "message": f"L'endpoint {request.url.path} n'existe pas",
            "endpoints_disponibles": [
                "GET /api/v1/health",
                "GET /api/v1/version",
                "POST /api/v1/score"
            ]
        }
    )


@app.exception_handler(422)
async def validation_error_handler(request: Request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "erreur": "DONNÉES_INVALIDES",
            "message": "Le payload JSON ne respecte pas le format attendu",
            "detail": str(exc)
        }
    )


# ═══════════════════════════════════════════════════════════════
# LANCEMENT DU SERVEUR
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    print("""
╔══════════════════════════════════════════════════════════════╗
║              KIFFA-SCORE API — DÉMARRAGE                     ║
║              Version 1.0.0 | In-Memory | Sécurisé            ║
╚══════════════════════════════════════════════════════════════╝
    """)
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )