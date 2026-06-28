"""
╔══════════════════════════════════════════════════════════════╗
║           KIFFA-SCORE — MOTEUR MATHÉMATIQUE DE SCORING       ║
║           Module : scoring.py                                ║
║           Version : 1.0.0                                    ║
║           Architecture : In-Memory | Zéro persistance        ║
╚══════════════════════════════════════════════════════════════╝

Description :
    Moteur de calcul du score de solvabilité Kiffa.
    Chaque pilier est une fonction indépendante,
    testable et auditable séparément.

Piliers :
    - Consistance  (Cf) : 40 points
    - Fréquence    (Fa) : 20 points  
    - Résilience   (Rs) : 20 points
    - Moralité     (Mf) : 20 points
    
TOTAL : 100 points
"""

from typing import List, Dict, Any
from datetime import datetime, date
from collections import defaultdict
import math


# ═══════════════════════════════════════════════════════════════
# PILIER 1 — CONSISTANCE (Cf) — 40 POINTS
# Évalue le volume total des entrées sur la période analysée.
# Seuils calibrés pour le marché camerounais :
#   > 1 000 000 FCFA  → 40 pts (PME solide)
#   > 500 000 FCFA    → 30 pts (Commerce actif)
#   > 300 000 FCFA    → 20 pts (Petit commerce)
#   > 100 000 FCFA    → 10 pts (Activité minimale)
#   ≤ 100 000 FCFA    →  5 pts (Risque élevé)
# ═══════════════════════════════════════════════════════════════

def calculer_consistance(transactions: List[Dict]) -> Dict[str, Any]:
    """
    Calcule le pilier Consistance basé sur le volume total des entrées.
    
    Args:
        transactions: Liste des transactions nettoyées (après anti-fraude)
    
    Returns:
        Dict contenant le score, le volume total et le détail du calcul
    """
    
    # Extraire uniquement les transactions entrantes
    entrees = [t for t in transactions if t['type'] == 'IN']
    volume_total = sum(t['amount'] for t in entrees)
    
    # Barème de scoring calibré Cameroun
    if volume_total > 1_000_000:
        score = 40
        niveau = "EXCELLENT — PME solide"
    elif volume_total > 500_000:
        # Interpolation linéaire entre 500k et 1M
        score = 30 + int((volume_total - 500_000) / 500_000 * 10)
        score = min(score, 39)
        niveau = "BON — Commerce actif"
    elif volume_total > 300_000:
        score = 20 + int((volume_total - 300_000) / 200_000 * 10)
        score = min(score, 29)
        niveau = "MOYEN — Petit commerce"
    elif volume_total > 100_000:
        score = 10 + int((volume_total - 100_000) / 200_000 * 10)
        score = min(score, 19)
        niveau = "FAIBLE — Activité minimale"
    else:
        score = 5
        niveau = "CRITIQUE — Risque élevé"
    
    return {
        "pilier": "Consistance (Cf)",
        "score": score,
        "max": 40,
        "volume_total_fcfa": volume_total,
        "nombre_entrees": len(entrees),
        "niveau": niveau,
        "detail": f"Volume total IN : {volume_total:,} FCFA → {score}/40 pts"
    }


# ═══════════════════════════════════════════════════════════════
# PILIER 2 — FRÉQUENCE (Fa) — 20 POINTS
# Évalue la régularité de l'activité commerciale.
# Compte le nombre de jours UNIQUES avec au moins 1 transaction.
# Seuils :
#   > 22 jours actifs → 20 pts (Activité quotidienne)
#   > 15 jours actifs → 15 pts (Activité régulière)
#   > 10 jours actifs → 10 pts (Activité correcte)
#   >  5 jours actifs →  5 pts (Activité faible)
#   ≤  5 jours actifs →  0 pts (Activité fantôme)
# ═══════════════════════════════════════════════════════════════

def calculer_frequence(transactions: List[Dict]) -> Dict[str, Any]:
    """
    Calcule le pilier Fréquence basé sur les jours d'activité uniques.
    
    Args:
        transactions: Liste des transactions nettoyées
    
    Returns:
        Dict contenant le score, le nombre de jours actifs et le détail
    """
    
    # Extraire les dates uniques de toutes les transactions
    jours_actifs = set()
    
    for t in transactions:
        try:
            # Parser le timestamp ISO 8601
            if isinstance(t['timestamp'], str):
                dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
            else:
                dt = t['timestamp']
            jours_actifs.add(dt.date())
        except (ValueError, KeyError):
            continue
    
    nb_jours = len(jours_actifs)
    
    # Barème de scoring
    if nb_jours > 22:
        score = 20
        niveau = "EXCELLENT — Activité quotidienne"
    elif nb_jours > 15:
        score = 15
        niveau = "BON — Activité régulière"
    elif nb_jours > 10:
        score = 10
        niveau = "MOYEN — Activité correcte"
    elif nb_jours > 5:
        score = 5
        niveau = "FAIBLE — Activité irrégulière"
    else:
        score = 0
        niveau = "CRITIQUE — Activité fantôme"
    
    return {
        "pilier": "Fréquence (Fa)",
        "score": score,
        "max": 20,
        "jours_actifs": nb_jours,
        "jours_uniques": [str(j) for j in sorted(jours_actifs)],
        "niveau": niveau,
        "detail": f"{nb_jours} jours actifs uniques → {score}/20 pts"
    }


# ═══════════════════════════════════════════════════════════════
# PILIER 3 — RÉSILIENCE (Rs) — 20 POINTS
# Évalue la capacité du commerçant à maintenir un solde positif.
# Analyse le 'balance_after' de fin de journée.
# Règle :
#   Base : 20 pts
#   -4 pts chaque fois que le solde fin de journée = 0
#   -2 pts chaque fois que le solde < 5 000 FCFA
#   Minimum : 0 pts
# ═══════════════════════════════════════════════════════════════

def calculer_resilience(transactions: List[Dict]) -> Dict[str, Any]:
    """
    Calcule le pilier Résilience basé sur l'analyse des soldes journaliers.
    
    Args:
        transactions: Liste des transactions nettoyées
    
    Returns:
        Dict contenant le score, les incidents de solde et le détail
    """
    
    # Grouper les transactions par jour et prendre le dernier solde
    soldes_journaliers = defaultdict(list)
    
    for t in transactions:
        try:
            if isinstance(t['timestamp'], str):
                dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
            else:
                dt = t['timestamp']
            jour = dt.date()
            soldes_journaliers[jour].append({
                'timestamp': dt,
                'balance_after': t.get('balance_after', 0)
            })
        except (ValueError, KeyError):
            continue
    
    score = 20
    incidents_zero = 0
    incidents_faible = 0
    details_incidents = []
    
    for jour, transactions_jour in soldes_journaliers.items():
        # Prendre le solde de la dernière transaction de la journée
        derniere_transaction = max(transactions_jour, key=lambda x: x['timestamp'])
        solde_fin = derniere_transaction['balance_after']
        
        if solde_fin == 0:
            score -= 4
            incidents_zero += 1
            details_incidents.append(f"{jour} : Solde = 0 FCFA (-4 pts)")
        elif solde_fin < 5_000:
            score -= 2
            incidents_faible += 1
            details_incidents.append(f"{jour} : Solde = {solde_fin:,} FCFA < 5,000 (-2 pts)")
    
    score = max(0, score)
    
    if score >= 18:
        niveau = "EXCELLENT — Trésorerie saine"
    elif score >= 14:
        niveau = "BON — Trésorerie stable"
    elif score >= 10:
        niveau = "MOYEN — Quelques tensions"
    elif score >= 5:
        niveau = "FAIBLE — Trésorerie fragile"
    else:
        niveau = "CRITIQUE — Trésorerie en danger"
    
    return {
        "pilier": "Résilience (Rs)",
        "score": score,
        "max": 20,
        "incidents_solde_zero": incidents_zero,
        "incidents_solde_faible": incidents_faible,
        "details_incidents": details_incidents,
        "niveau": niveau,
        "detail": f"{incidents_zero} jours à zéro, {incidents_faible} jours < 5,000 FCFA → {score}/20 pts"
    }


# ═══════════════════════════════════════════════════════════════
# PILIER 4 — MORALITÉ FINANCIÈRE (Mf) — 20 POINTS
# Évalue la discipline dans le paiement des charges fixes.
# Règle :
#   Au moins 1 paiement is_utility_bill=true par mois → 20 pts
#   Paiements présents mais irréguliers → 10 pts
#   Aucun paiement de facture détecté → 0 pts
# ═══════════════════════════════════════════════════════════════

def calculer_moralite(transactions: List[Dict]) -> Dict[str, Any]:
    """
    Calcule le pilier Moralité basé sur les paiements de factures.
    
    Args:
        transactions: Liste des transactions nettoyées
    
    Returns:
        Dict contenant le score, les mois couverts et le détail
    """
    
    # Extraire les paiements de factures (Eneo, Camwater, etc.)
    paiements_factures = [
        t for t in transactions 
        if t.get('is_utility_bill', False) and t['type'] == 'OUT'
    ]
    
    if not paiements_factures:
        return {
            "pilier": "Moralité (Mf)",
            "score": 0,
            "max": 20,
            "paiements_factures": 0,
            "mois_couverts": [],
            "niveau": "CRITIQUE — Aucun paiement de facture détecté",
            "detail": "0 paiement de facture → 0/20 pts"
        }
    
    # Grouper par mois
    mois_avec_factures = set()
    for t in paiements_factures:
        try:
            if isinstance(t['timestamp'], str):
                dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
            else:
                dt = t['timestamp']
            mois_avec_factures.add((dt.year, dt.month))
        except (ValueError, KeyError):
            continue
    
    # Déterminer les mois total de la période analysée
    toutes_dates = []
    for t in transactions:
        try:
            if isinstance(t['timestamp'], str):
                dt = datetime.fromisoformat(t['timestamp'].replace('Z', '+00:00'))
            else:
                dt = t['timestamp']
            toutes_dates.append((dt.year, dt.month))
        except:
            continue
    
    mois_total = len(set(toutes_dates)) if toutes_dates else 1
    mois_couverts = len(mois_avec_factures)
    
    # Scoring basé sur la couverture mensuelle
    if mois_couverts >= mois_total:
        score = 20
        niveau = "EXCELLENT — Factures payées chaque mois"
    elif mois_couverts >= mois_total * 0.5:
        score = 10
        niveau = "MOYEN — Paiements irréguliers"
    else:
        score = 5
        niveau = "FAIBLE — Très peu de factures payées"
    
    mois_labels = [f"{m[0]}-{str(m[1]).zfill(2)}" for m in sorted(mois_avec_factures)]
    
    return {
        "pilier": "Moralité (Mf)",
        "score": score,
        "max": 20,
        "paiements_factures": len(paiements_factures),
        "mois_couverts": mois_labels,
        "mois_total_periode": mois_total,
        "niveau": niveau,
        "detail": f"{mois_couverts}/{mois_total} mois avec factures → {score}/20 pts"
    }


# ═══════════════════════════════════════════════════════════════
# ORCHESTRATEUR — CALCUL FINAL DU SCORE KIFFA
# Agrège les 4 piliers et applique les malus anti-fraude
# ═══════════════════════════════════════════════════════════════

def calculer_score_final(
    transactions: List[Dict],
    malus_concentration: float = 0.0
) -> Dict[str, Any]:
    """
    Orchestre le calcul complet du score Kiffa-Score.
    
    Args:
        transactions: Liste des transactions nettoyées par l'anti-fraude
        malus_concentration: Malus appliqué si fraude circulaire détectée (0.0 à 1.0)
    
    Returns:
        Dict complet avec score final, détail des piliers et décision
    """
    
    # Calcul des 4 piliers
    consistance = calculer_consistance(transactions)
    frequence = calculer_frequence(transactions)
    resilience = calculer_resilience(transactions)
    moralite = calculer_moralite(transactions)
    
    # Score brut
    score_brut = (
        consistance['score'] +
        frequence['score'] +
        resilience['score'] +
        moralite['score']
    )
    
    # Application du malus de concentration (fraude circulaire)
    score_final = int(score_brut * (1 - malus_concentration))
    score_final = max(0, min(100, score_final))
    
    # Décision finale
    if score_final >= 80:
        mention = "EXCELLENT"
        decision = "APPROUVÉ"
        montant_max_fcfa = 500_000
        couleur = "#16A34A"
    elif score_final >= 70:
        mention = "BON"
        decision = "APPROUVÉ"
        montant_max_fcfa = 200_000
        couleur = "#1A56DB"
    elif score_final >= 50:
        mention = "MOYEN"
        decision = "APPROUVÉ AVEC CAUTION"
        montant_max_fcfa = 50_000
        couleur = "#D97706"
    elif score_final >= 30:
        mention = "FAIBLE"
        decision = "DOSSIER À REVOIR"
        montant_max_fcfa = 0
        couleur = "#EA580C"
    else:
        mention = "CRITIQUE"
        decision = "REJETÉ"
        montant_max_fcfa = 0
        couleur = "#DC2626"
    
    return {
        "kiffa_score": score_final,
        "score_brut": score_brut,
        "malus_concentration_applique": malus_concentration > 0,
        "mention": mention,
        "decision": decision,
        "montant_max_recommande_fcfa": montant_max_fcfa,
        "couleur_decision": couleur,
        "piliers": {
            "consistance": consistance,
            "frequence": frequence,
            "resilience": resilience,
            "moralite": moralite
        },
        "resume": f"Score {score_final}/100 — {mention} — {decision}"
    }