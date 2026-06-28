"""
╔══════════════════════════════════════════════════════════════╗
║         KIFFA-SCORE — MODULE ANTI-FRAUDE                     ║
║         Module : anti_fraude.py                              ║
║         Version : 1.0.0                                      ║
║         Architecture : In-Memory | Zéro persistance          ║
╚══════════════════════════════════════════════════════════════╝

Description :
    Module de nettoyage et détection de fraude.
    S'exécute AVANT le moteur de scoring.
    Deux filtres principaux :
    
    1. Filtre de Vélocité  : Détecte les fonds de transit
    2. Filtre de Concentration : Détecte la fraude circulaire

IMPORTANT SÉCURITÉ :
    Ce module ne stocke AUCUNE donnée.
    Toutes les opérations sont In-Memory.
    Les données sont purgées par le Garbage Collector
    dès que la fonction retourne son résultat.
"""

from typing import List, Dict, Any, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import gc


# ═══════════════════════════════════════════════════════════════
# UTILITAIRE — PARSER LES TIMESTAMPS
# ═══════════════════════════════════════════════════════════════

def _parser_timestamp(timestamp) -> datetime:
    """
    Parse un timestamp ISO 8601 en objet datetime.
    Gère les formats avec et sans timezone.
    
    Args:
        timestamp: String ISO 8601 ou objet datetime
    
    Returns:
        Objet datetime
    
    Raises:
        ValueError si le format est invalide
    """
    if isinstance(timestamp, datetime):
        return timestamp
    
    # Normaliser le format Z → +00:00
    ts_clean = str(timestamp).replace('Z', '+00:00')
    
    try:
        return datetime.fromisoformat(ts_clean)
    except ValueError:
        # Fallback pour formats non-standard
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f"
        ]
        for fmt in formats:
            try:
                return datetime.strptime(str(timestamp)[:19], fmt)
            except ValueError:
                continue
        raise ValueError(f"Format timestamp invalide : {timestamp}")


# ═══════════════════════════════════════════════════════════════
# FILTRE 1 — VÉLOCITÉ
# Détecte et exclut les fonds de "transit"
# Définition : Transaction IN suivie d'une transaction OUT
# d'un montant similaire (±5%) dans un délai < 30 minutes.
# Ces transactions ne représentent pas du chiffre d'affaires réel.
# ═══════════════════════════════════════════════════════════════

def filtre_velocite(
    transactions: List[Dict],
    delai_minutes: int = 30,
    marge_pourcentage: float = 0.05
) -> Tuple[List[Dict], Dict[str, Any]]:
    """
    Filtre les transactions de transit (IN → OUT rapide).
    
    Args:
        transactions: Liste brute des transactions
        delai_minutes: Délai max entre IN et OUT pour considérer transit (défaut: 30 min)
        marge_pourcentage: Tolérance sur le montant (défaut: 5%)
    
    Returns:
        Tuple (transactions_propres, rapport_filtre)
    """
    
    # Trier par timestamp
    try:
        transactions_triees = sorted(
            transactions,
            key=lambda t: _parser_timestamp(t['timestamp'])
        )
    except Exception as e:
        return transactions, {
            "filtre": "velocite",
            "statut": "ERREUR",
            "message": f"Erreur tri : {str(e)}",
            "transactions_exclues": 0
        }
    
    # IDs des transactions à exclure
    ids_a_exclure = set()
    paires_transit = []
    
    # Pour chaque transaction IN, chercher un OUT correspondant
    for i, transaction_in in enumerate(transactions_triees):
        if transaction_in['type'] != 'IN':
            continue
        if transaction_in['transaction_id'] in ids_a_exclure:
            continue
            
        montant_in = transaction_in['amount']
        timestamp_in = _parser_timestamp(transaction_in['timestamp'])
        limite_temps = timestamp_in + timedelta(minutes=delai_minutes)
        
        # Chercher un OUT correspondant dans la fenêtre temporelle
        for transaction_out in transactions_triees[i+1:]:
            timestamp_out = _parser_timestamp(transaction_out['timestamp'])
            
            # Sortir de la boucle si on dépasse la fenêtre temporelle
            if timestamp_out > limite_temps:
                break
            
            if transaction_out['type'] != 'OUT':
                continue
            if transaction_out['transaction_id'] in ids_a_exclure:
                continue
            
            montant_out = transaction_out['amount']
            
            # Vérifier si les montants sont similaires (±marge)
            difference = abs(montant_in - montant_out) / montant_in
            if difference <= marge_pourcentage:
                # Transit détecté — exclure les deux transactions
                ids_a_exclure.add(transaction_in['transaction_id'])
                ids_a_exclure.add(transaction_out['transaction_id'])
                
                paires_transit.append({
                    "in_id": transaction_in['transaction_id'],
                    "out_id": transaction_out['transaction_id'],
                    "montant_in": montant_in,
                    "montant_out": montant_out,
                    "delai_minutes": round(
                        (timestamp_out - timestamp_in).seconds / 60, 1
                    )
                })
                break
    
    # Filtrer les transactions
    transactions_propres = [
        t for t in transactions
        if t['transaction_id'] not in ids_a_exclure
    ]
    
    rapport = {
        "filtre": "velocite",
        "statut": "OK",
        "transactions_initiales": len(transactions),
        "transactions_exclues": len(ids_a_exclure),
        "transactions_restantes": len(transactions_propres),
        "paires_transit_detectees": len(paires_transit),
        "detail_paires": paires_transit,
        "message": (
            f"{len(paires_transit)} paires de transit détectées — "
            f"{len(ids_a_exclure)} transactions exclues du calcul"
        )
    }
    
    return transactions_propres, rapport


# ═══════════════════════════════════════════════════════════════
# FILTRE 2 — CONCENTRATION
# Détecte la fraude circulaire entre complices.
# Si les 3 plus gros émetteurs représentent > 80% du volume IN
# → Malus de 50% sur le score final
# Logique : Un vrai commerçant reçoit de nombreux petits clients,
# pas 80% de ses revenus de 3 personnes.
# ═══════════════════════════════════════════════════════════════

def filtre_concentration(
    transactions: List[Dict],
    seuil_concentration: float = 0.80,
    nb_top_counterparties: int = 3
) -> Tuple[float, Dict[str, Any]]:
    """
    Calcule le niveau de concentration des sources de revenus.
    
    Args:
        transactions: Liste des transactions (après filtre vélocité)
        seuil_concentration: Seuil au-delà duquel appliquer le malus (défaut: 80%)
        nb_top_counterparties: Nombre de contreparties à analyser (défaut: 3)
    
    Returns:
        Tuple (malus_a_appliquer, rapport_filtre)
        malus_a_appliquer : 0.0 (pas de malus) ou 0.5 (malus 50%)
    """
    
    # Extraire uniquement les transactions entrantes
    entrees = [t for t in transactions if t['type'] == 'IN']
    
    if not entrees:
        return 0.0, {
            "filtre": "concentration",
            "statut": "OK",
            "message": "Aucune transaction IN — pas de calcul de concentration",
            "malus_applique": False,
            "malus_valeur": 0.0
        }
    
    # Calculer le volume par contrepartie
    volume_par_contrepartie = defaultdict(int)
    for t in entrees:
        counterparty = t.get('counterparty_id', 'INCONNU')
        # Anonymiser pour les logs — on ne stocke pas les vrais IDs
        volume_par_contrepartie[counterparty] += t['amount']
    
    # Volume total entrant
    volume_total = sum(volume_par_contrepartie.values())
    
    if volume_total == 0:
        return 0.0, {
            "filtre": "concentration",
            "statut": "OK",
            "message": "Volume total nul",
            "malus_applique": False,
            "malus_valeur": 0.0
        }
    
    # Top N contreparties par volume
    top_contreparties = sorted(
        volume_par_contrepartie.items(),
        key=lambda x: x[1],
        reverse=True
    )[:nb_top_counterparties]
    
    # Volume des top N contreparties
    volume_top_n = sum(v for _, v in top_contreparties)
    taux_concentration = volume_top_n / volume_total
    
    # Décision malus
    malus = 0.5 if taux_concentration > seuil_concentration else 0.0
    
    # Préparer le rapport sans exposer les vrais IDs
    top_anonymises = [
        {
            "rang": i + 1,
            "pourcentage_volume": round(v / volume_total * 100, 1)
        }
        for i, (_, v) in enumerate(top_contreparties)
    ]
    
    rapport = {
        "filtre": "concentration",
        "statut": "ALERTE" if malus > 0 else "OK",
        "nombre_contreparties_uniques": len(volume_par_contrepartie),
        "taux_concentration_top3": round(taux_concentration * 100, 1),
        "seuil_alerte_pourcentage": seuil_concentration * 100,
        "top_contreparties_anonymisees": top_anonymises,
        "malus_applique": malus > 0,
        "malus_valeur": malus,
        "message": (
            f"Top {nb_top_counterparties} contreparties = "
            f"{taux_concentration*100:.1f}% du volume IN — "
            f"{'MALUS 50% APPLIQUÉ' if malus > 0 else 'Pas de malus'}"
        )
    }
    
    # Purger les données sensibles de la mémoire
    del volume_par_contrepartie
    del top_contreparties
    gc.collect()
    
    return malus, rapport


# ═══════════════════════════════════════════════════════════════
# ORCHESTRATEUR ANTI-FRAUDE
# Exécute les deux filtres en séquence et retourne
# les transactions nettoyées + le rapport complet
# ═══════════════════════════════════════════════════════════════

def executer_anti_fraude(transactions: List[Dict]) -> Dict[str, Any]:
    """
    Point d'entrée principal du module anti-fraude.
    Exécute les filtres en séquence :
    1. Filtre Vélocité → nettoie les transactions
    2. Filtre Concentration → calcule le malus éventuel
    
    Args:
        transactions: Liste brute des transactions reçues par l'API
    
    Returns:
        Dict contenant :
        - transactions_nettoyees: Liste filtrée pour le scoring
        - malus_concentration: Float (0.0 ou 0.5)
        - rapport_velocite: Détail du filtre vélocité
        - rapport_concentration: Détail du filtre concentration
        - score_fraude_global: Score de confiance anti-fraude /100
        - statut_global: PROPRE / SUSPECT / REJETÉ
    """
    
    print(f"\n[ANTI-FRAUDE] Analyse de {len(transactions)} transactions...")
    
    # ── ÉTAPE 1 : Filtre Vélocité ──
    transactions_nettoyees, rapport_velocite = filtre_velocite(transactions)
    print(f"[ANTI-FRAUDE] Vélocité : {rapport_velocite['message']}")
    
    # ── ÉTAPE 2 : Filtre Concentration ──
    malus, rapport_concentration = filtre_concentration(transactions_nettoyees)
    print(f"[ANTI-FRAUDE] Concentration : {rapport_concentration['message']}")
    
    # ── CALCUL DU SCORE DE CONFIANCE ANTI-FRAUDE ──
    score_confiance = 100
    
    # Pénalité pour transactions de transit
    nb_transit = rapport_velocite.get('paires_transit_detectees', 0)
    if nb_transit > 0:
        penalite_transit = min(30, nb_transit * 10)
        score_confiance -= penalite_transit
    
    # Pénalité pour concentration
    if malus > 0:
        score_confiance -= 40
    
    score_confiance = max(0, score_confiance)
    
    # Statut global
    if score_confiance >= 80:
        statut_global = "✅ PROPRE"
    elif score_confiance >= 50:
        statut_global = "⚠️ SUSPECT — Vérification recommandée"
    else:
        statut_global = "🚨 REJETÉ — Fraude probable"
    
    resultat = {
        "transactions_nettoyees": transactions_nettoyees,
        "malus_concentration": malus,
        "rapport_velocite": rapport_velocite,
        "rapport_concentration": rapport_concentration,
        "score_confiance_anti_fraude": score_confiance,
        "statut_global": statut_global,
        "nb_transactions_initiales": len(transactions),
        "nb_transactions_analysees": len(transactions_nettoyees)
    }
    
    # Purge mémoire
    del transactions
    gc.collect()
    
    print(f"[ANTI-FRAUDE] Score confiance : {score_confiance}/100 — {statut_global}")
    
    return resultat