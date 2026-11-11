"""Demo data for FoodBridge.   Run from the backend/ folder:   python seed.py [--yes]

WARNING: this DELETES everything in the database and recreates demo data (asks first if data exists).
All times are relative to "now", so the demo is always fresh (re-run it before a demo).

Demo scenarios (Bengaluru):
  * ABC College  : 120 vegetarian meals, 2 h left  -> 3 eligible NGOs with clearly different scores,
                   1 excluded for capacity (Little Hands), 1 never considered (unverified).
  * Green Leaf   : 45 non-veg portions, 50 min left -> Hope Shelter excluded (veg only), Seva Food Bank
                   excluded (cannot arrive in time). Urgency matters.
  * Lakeview     : 150 vegetarian meals, 3 h left    -> a different ranking again.
"""
import sys
from datetime import timedelta

from app.auth.security import hash_password
from app.database.session import Base, SessionLocal, engine
from app.matching.engine import compute_matches
from app.models import Donation, ImpactRecord, Match, Organization, Pickup, Report, User
from app.services.impact_calc import calc_impact_figures
from app.utils.time import utcnow

DEMO_PASSWORDS = {"admin": "Admin@FoodBridge1", "donor": "Donor@FoodBridge1", "ngo": "Ngo@FoodBridge1"}

now = utcnow()
H = lambda hours: timedelta(hours=hours)
M = lambda minutes: timedelta(minutes=minutes)
D = lambda days: timedelta(days=days)


def _confirm_wipe() -> bool:
    """Refuse to delete an existing database unless the user says so (or passes --yes)."""
    from sqlalchemy import inspect
    if not inspect(engine).has_table("users"):
        return True
    if "--yes" in sys.argv or "-y" in sys.argv:
        return True
    if not sys.stdin.isatty():
        print("The database already contains data. Re-run with --yes to delete it and recreate the demo data.")
        return False
    return input(f"This will DELETE all data in {engine.url}. Type 'yes' to continue: ").strip().lower() == "yes"


def main() -> None:
    if not _confirm_wipe():
        print("Cancelled. Nothing was changed.")
        sys.exit(1)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    hashes = {k: hash_password(v) for k, v in DEMO_PASSWORDS.items()}

    # ------------------------------------------------------------------ users
    def user(email, name, role, kind, phone, donor_type=None):
        u = User(email=email, full_name=name, role=role, password_hash=hashes[kind], phone=phone,
                 donor_type=donor_type, created_at=now - D(90))
        db.add(u)
        return u

    admin = user("admin@foodbridge.demo", "Platform Admin", "ADMIN", "admin", "+91 80 0000 0000")
    restaurant = user("restaurant@foodbridge.demo", "Green Leaf Restaurant", "DONOR", "donor", "+91 98450 11111", "RESTAURANT")
    college = user("college@foodbridge.demo", "ABC College of Engineering", "DONOR", "donor", "+91 98450 22222", "COLLEGE_EVENT")
    hotel = user("hotel@foodbridge.demo", "Lakeview Residency Hotel", "DONOR", "donor", "+91 98450 33333", "HOTEL")

    def ngo(n, email_user_name, org_name, org_type, contact, lat, lng, address, radius, capacity,
            categories, diet, hours, status):
        u = user(f"ngo{n}@foodbridge.demo", email_user_name, "ORGANIZATION", "ngo", f"+91 98860 0{n}000")
        o = Organization(user=u, name=org_name, organization_type=org_type, contact_person=contact,
                         phone=u.phone, address=address, latitude=lat, longitude=lng,
                         service_radius_km=radius, max_capacity_meals=capacity,
                         preferred_categories=categories, food_preferences=diet, operating_hours=hours,
                         verification_status=status, created_at=now - D(80))
        db.add(o)
        return o

    ngo1 = ngo(1, "Lakshmi Rao", "Annapurna Community Kitchen", "COMMUNITY_KITCHEN", "Lakshmi Rao",
               12.9279, 77.6271, "5th Block, Koramangala, Bengaluru", 12, 300,
               ["COOKED_MEALS", "BAKERY"], "BOTH", "08:00-22:00", "VERIFIED")
    ngo2 = ngo(2, "Fr. Thomas Mathew", "Hope Shelter Home", "SHELTER", "Thomas Mathew",
               12.9857, 77.6057, "Shivajinagar, Bengaluru", 10, 150,
               ["COOKED_MEALS", "PACKAGED_FOOD", "FRUITS_VEGETABLES"], "VEG_ONLY", "09:00-18:00", "VERIFIED")
    ngo3 = ngo(3, "Imran Sheikh", "Seva Food Bank", "FOOD_DISTRIBUTION", "Imran Sheikh",
               12.9698, 77.7500, "Whitefield Main Road, Bengaluru", 25, 800,
               [], "BOTH", "07:00-23:00", "VERIFIED")
    ngo(4, "Meera Pillai", "Little Hands Children's Home", "NGO", "Meera Pillai",
               12.9116, 77.6474, "HSR Layout Sector 2, Bengaluru", 8, 60,
               ["COOKED_MEALS", "DAIRY", "BAKERY"], "BOTH", "10:00-17:00", "VERIFIED")
    ngo(5, "Arun Nair", "New Dawn Trust (awaiting verification)", "NGO", "Arun Nair",
               12.9500, 77.6000, "Richmond Town, Bengaluru", 15, 400,
               [], "BOTH", "09:00-20:00", "PENDING")
    db.flush()

    # ------------------------------------------------------------------ helpers
    locations = {
        restaurant.id: (12.9784, 77.6408, "Green Leaf Restaurant, 100 Feet Road, Indiranagar, Bengaluru"),
        college.id: (12.9352, 77.6245, "ABC College of Engineering, Koramangala, Bengaluru"),
        hotel.id: (12.9756, 77.6068, "Lakeview Residency, MG Road, Bengaluru"),
    }

    def donation(donor, title, category, veg, qty, unit, meals, created_ago, window, desc=None, notes=None):
        """created_ago: how long ago it was posted. window: time from posting until it expires."""
        lat, lng, address = locations[donor.id]
        created = now - created_ago
        d = Donation(donor=donor, title=title, category=category, is_vegetarian=veg, quantity=qty, unit=unit,
                     estimated_meals=meals, prepared_at=created - M(30), available_until=created + window,
                     pickup_address=address, latitude=lat, longitude=lng, description=desc,
                     handling_notes=notes, contact_info=f"{donor.full_name}: {donor.phone}",
                     status="AVAILABLE", created_at=created)
        db.add(d)
        db.flush()
        return d

    def accept(d, org, scheduled=None, collected=None, completed=None, notes=None):
        db.add(Pickup(donation_id=d.id, organization_id=org.id, scheduled_at=scheduled,
                      collected_at=collected, completed_at=completed, notes=notes))

    # ------------------------------------------------------------------ committed donations (create org "load")
    d_acc = donation(hotel, "Fresh bread & buns", "BAKERY", True, 60, "PACKETS", 60, H(1), H(6),
                     desc="Baked this morning, packed in sealed bags.", notes="Keep dry. Pickup from the service entrance.")
    compute_matches(db, d_acc, now=d_acc.created_at)
    d_acc.status = "ACCEPTED"
    accept(d_acc, ngo1)

    d_pick = donation(college, "Seminar lunch boxes", "COOKED_MEALS", True, 90, "MEALS", 90, H(1), H(5),
                      desc="Veg pulao + raita boxes left from a seminar.", notes="Boxes sealed at 12:30. Refrigerate if possible.")
    compute_matches(db, d_pick, now=d_pick.created_at)
    d_pick.status = "PICKUP_PENDING"
    accept(d_pick, ngo3, scheduled=now + M(45), notes="Van arriving at the main gate.")

    d_col = donation(restaurant, "Dal, rice & mixed vegetables", "COOKED_MEALS", True, 45, "PORTIONS", 45, H(3), H(5),
                     desc="Lunch service surplus, kept hot.", notes="Packed in insulated containers.")
    compute_matches(db, d_col, now=d_col.created_at)
    d_col.status = "COLLECTED"
    accept(d_col, ngo1, scheduled=now - H(1), collected=now - M(25))

    # ------------------------------------------------------------------ completed donations (history + trends)
    completed_plan = [  # (days ago, donor, organization, meals, title)
        (2, restaurant, ngo1, 60, "Lunch buffet leftovers"),
        (5, college, ngo3, 120, "Cultural fest dinner"),
        (8, hotel, ngo2, 45, "Breakfast spread"),
        (12, restaurant, ngo1, 80, "Biryani trays"),
        (16, college, ngo3, 200, "Alumni meet catering"),
        (21, hotel, ngo1, 90, "Conference lunch"),
        (26, restaurant, ngo2, 50, "Veg thali surplus"),
        (33, college, ngo1, 150, "Hackathon dinner"),
        (41, hotel, ngo3, 110, "Wedding reception surplus"),
        (49, restaurant, ngo1, 70, "Weekend brunch"),
        (72, college, ngo3, 180, "Sports day lunch"),
        (98, hotel, ngo1, 130, "Banquet leftovers"),
        (125, restaurant, ngo2, 60, "Festival feast surplus"),
        (150, college, ngo3, 100, "Orientation lunch"),
    ]
    completed = []
    for days_ago, donor, org, meals, title in completed_plan:
        d = donation(donor, title, "COOKED_MEALS", True, meals, "MEALS", meals, D(days_ago), H(4))
        d.status = "COMPLETED"
        done_at = d.created_at + H(2.5)
        accept(d, org, scheduled=d.created_at + H(1.5), collected=d.created_at + H(2), completed=done_at)
        m, kg, people = calc_impact_figures(d.estimated_meals, d.quantity, d.unit)
        db.add(ImpactRecord(donation_id=d.id, meals_redistributed=m, quantity_kg_est=kg,
                            people_served_est=people, created_at=done_at))
        completed.append(d)

    # ------------------------------------------------------------------ cancelled & expired
    d_can = donation(college, "Evening snack packets", "SNACKS", True, 30, "PACKETS", 30, H(26), H(4))
    d_can.status = "CANCELLED"
    d_exp = donation(restaurant, "Pasta trays", "COOKED_MEALS", True, 25, "PORTIONS", 25, H(7), H(3),
                     desc="Nobody could collect in time.")
    d_exp.status = "EXPIRED"
    db.commit()   # NGO load is now visible to the matching engine

    # ------------------------------------------------------------------ OPEN donations (the matching showcase)
    d_college = donation(college, "Annual fest: vegetarian meals", "COOKED_MEALS", True, 120, "MEALS", 120,
                         M(10), H(2),
                         desc="Veg biryani, curd rice and sweets left after the annual fest. Freshly cooked, kept covered.",
                         notes="Please bring your own containers or vehicle. Contact the fest desk on arrival.")
    d_rest = donation(restaurant, "Chicken biryani & raita", "COOKED_MEALS", False, 45, "PORTIONS", 45,
                      M(5), M(50),
                      desc="Dinner-service surplus, cooked 1 hour ago and kept above 60 C.",
                      notes="Urgent: closing early today. Pickup at the back door.")
    d_hotel = donation(hotel, "Conference lunch: vegetarian buffet", "COOKED_MEALS", True, 150, "MEALS", 150,
                       M(15), H(3),
                       desc="Mixed vegetarian buffet from a corporate event.", notes="Hot-holding until pickup.")
    for d in (d_college, d_rest, d_hotel):
        compute_matches(db, d)
    db.commit()

    # ------------------------------------------------------------------ reports
    db.add(Report(donation_id=completed[3].id, reporter_id=ngo1.user_id, reason="INCORRECT_QUANTITY",
                  details="We received about 65 portions instead of the 80 listed.", status="OPEN",
                  created_at=now - H(20)))
    db.add(Report(donation_id=completed[1].id, reporter_id=college.id, reason="PICKUP_PROBLEM",
                  details="The collection van arrived 40 minutes later than agreed.", status="RESOLVED",
                  resolved_by=admin.id, resolution_notes="Spoke to the organization; they will confirm pickup windows earlier.",
                  created_at=now - D(3)))
    db.commit()

    # ------------------------------------------------------------------ summary
    print("\nFoodBridge demo data created.\n")
    print("Logins (email / password)")
    print(f"  Admin         admin@foodbridge.demo        / {DEMO_PASSWORDS['admin']}")
    for e in ("restaurant", "college", "hotel"):
        print(f"  Donor         {e}@foodbridge.demo".ljust(40) + f"/ {DEMO_PASSWORDS['donor']}")
    for n in range(1, 6):
        tag = " (unverified)" if n == 5 else ""
        print(f"  Organization  ngo{n}@foodbridge.demo".ljust(40) + f"/ {DEMO_PASSWORDS['ngo']}{tag}")

    print("\nMatching showcase (ranked organizations per open donation):")
    for d in (d_college, d_rest, d_hotel):
        rows = db.query(Match).filter(Match.donation_id == d.id).order_by(Match.rank).all()
        print(f"  {d.title} ({d.estimated_meals} meals):")
        for m in rows:
            print(f"    #{m.rank} {m.organization.name:<42} {m.score:5.1f}%  ({m.score_breakdown['distance_km']} km)")
    db.close()


if __name__ == "__main__":
    main()
