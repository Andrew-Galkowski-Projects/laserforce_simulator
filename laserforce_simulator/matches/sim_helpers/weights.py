def _get_medic_weights(player, action_to_weight_index, weights, all_alive, second):
    # change weights based on role
    weights[action_to_weight_index["tag_player"]] -= 70
    weights[action_to_weight_index["resupply_ally"]] += 70
    weights[action_to_weight_index["change_zone"]] -= 30
    weights[action_to_weight_index["hide"]] += 30

    # change weights based on zone/bases
    if player.can_capture_base_in_current_zone:
        weights[action_to_weight_index["resupply_ally"]] -= 50
        weights[
            action_to_weight_index["capture_base"]
        ] += 50  # When able, more likely to capture base
        weights[action_to_weight_index["hide"]] -= 20
        weights[action_to_weight_index["change_zone"]] += 20

    # change weights based on resources
    if player.final_lives <= 3:
        # when low on lives, more likely to resupply
        weights[action_to_weight_index["hide"]] -= 30
        weights[action_to_weight_index["resupply_ally"]] += 30

    if player.final_special >= player.special_cost:
        allies_active = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.final_lives > 0
            and p.is_active_at(second)
        ]
        weights[action_to_weight_index["use_special"]] += 20 * len(allies_active)

    # change weights based on active status
    if not player.is_active_at(second):
        # if not active and heavy in zone, then hide, else change zone
        heavy_in_zone = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "heavy"
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ),
            None,
        )
        if heavy_in_zone:
            weights[action_to_weight_index["resupply_ally"]] -= 70
            weights[action_to_weight_index["hide"]] += 70
        else:
            weights[action_to_weight_index["resupply_ally"]] -= 70
            weights[action_to_weight_index["change_zone"]] += 70
    return weights


def _get_ammo_weights(player, action_to_weight_index, weights, all_alive, second):
    # change weights based on role
    weights[action_to_weight_index["tag_player"]] -= 20
    weights[action_to_weight_index["change_zone"]] -= 30
    weights[action_to_weight_index["resupply_ally"]] += 50

    # change weights based on zone/bases
    if player.can_capture_base_in_current_zone:
        weights[action_to_weight_index["resupply_ally"]] -= 30
        weights[action_to_weight_index["tag_player"]] -= 20
        weights[
            action_to_weight_index["capture_base"]
        ] += 30  # When able, more likely to capture base
        weights[action_to_weight_index["change_zone"]] += 20

    # change weights based on resources
    if player.final_lives <= 3:
        # if medic is alive move towards medic, else hide or follow heavy
        medic_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.final_lives > 0
            ),
            None,
        )
        if medic_alive:
            if player.current_zone != medic_alive.current_zone:
                weights[action_to_weight_index["resupply_ally"]] -= 50
                weights[
                    action_to_weight_index["change_zone"]
                ] += 50  # Move towards medic
            else:
                weights[action_to_weight_index["resupply_ally"]] -= 30
                weights[
                    action_to_weight_index["hide"]
                ] += 30  # Stay with medic and hide
        else:
            heavy_alive = next(
                (
                    p
                    for p in all_alive
                    if p.team_color == player.team_color
                    and p.role == "heavy"
                    and p.final_lives > 0
                ),
                None,
            )
            if heavy_alive:
                if player.current_zone != heavy_alive.current_zone:
                    weights[action_to_weight_index["resupply_ally"]] -= 50
                    weights[
                        action_to_weight_index["change_zone"]
                    ] += 50  # Move towards heavy
                else:
                    weights[action_to_weight_index["resupply_ally"]] -= 50
                    weights[
                        action_to_weight_index["hide"]
                    ] += 50  # Stay with heavy and hide
            else:
                weights[action_to_weight_index["resupply_ally"]] -= 50
                weights[
                    action_to_weight_index["hide"]
                ] += 50  # No medic, no heavy, change zone to find safety

        # when low on lives, more likely to resupply
        weights[action_to_weight_index["tag_player"]] -= 20
        weights[action_to_weight_index["resupply_ally"]] += 20

    # change weights based on special resources
    if player.final_special >= player.special_cost:
        allies_active = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.final_lives > 0
            and p.is_active_at(second)
        ]
        weights[action_to_weight_index["use_special"]] += 20 * len(allies_active)

    # change weights based on active status
    if not player.is_active_at(second):
        # if not active and heavy in zone, then hide, else change zone
        heavy_in_zone = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "heavy"
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ),
            None,
        )
        if heavy_in_zone:
            weights[action_to_weight_index["resupply_ally"]] -= 70
            weights[action_to_weight_index["hide"]] += 70
        else:
            weights[action_to_weight_index["resupply_ally"]] -= 70
            weights[action_to_weight_index["change_zone"]] += 70
    return weights


def _get_scout_weights(player, action_to_weight_index, weights, all_alive, second):
    # change weights based on role
    weights[action_to_weight_index["tag_player"]] -= 10
    weights[action_to_weight_index["change_zone"]] += 10

    # change weights based on zone/bases
    if player.can_capture_base_in_current_zone:
        weights[action_to_weight_index["tag_player"]] -= 20
        weights[
            action_to_weight_index["capture_base"]
        ] += 20  # When able, more likely to capture base

    # change weights based on resources
    lives_critical = player.starting_lives * 0.3
    shots_critical = player.starting_shots * 0.3
    if player.final_lives <= lives_critical:
        # if medic is alive, determine medic zone and move towards medic,
        medic_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.final_lives > 0
            ),
            None,
        )
        if medic_alive:
            if player.current_zone != medic_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards medic
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 20  # scouts still want to move a bit
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[
                    action_to_weight_index["hide"]
                ] += 40  # Stay with medic and hide
        else:
            weights[action_to_weight_index["tag_player"]] -= 30
            weights[
                action_to_weight_index["hide"]
            ] += 30  # No medic, change zone to find safety
    if player.final_shots <= shots_critical:
        # if ammo is alive, determine ammo zone and move towards ammo,
        ammo_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "ammo"
                and p.final_lives > 0
            ),
            None,
        )
        if ammo_alive:
            if player.current_zone != ammo_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards ammo
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 20  # scouts still want to move a bit
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[action_to_weight_index["hide"]] += 40  # Stay with ammo and hide
        else:
            weights[action_to_weight_index["tag_player"]] -= 50
            weights[
                action_to_weight_index["hide"]
            ] += 50  # No ammo, change zone to find safety

    # change weights based on special resources
    if (
        player.final_special >= player.special_cost
        and player.special_active_until <= second
    ):
        # the more ammo a scout has the more likely they are to use their special
        weights[action_to_weight_index["use_special"]] += 100 * (
            player.final_shots / player.max_shots
        )

    # change weights based on active status
    if not player.is_active_at(second):
        # scouts are more likely to change zone or hide if not active
        weights[action_to_weight_index["tag_player"]] -= 60
        weights[action_to_weight_index["change_zone"]] += 10
        weights[action_to_weight_index["hide"]] += 50
    return weights


def _get_heavy_weights(player, action_to_weight_index, weights, all_alive, second):
    # change weights based on role
    # heavies are more likely to tag than change zone or hide, can missile
    weights[action_to_weight_index["change_zone"]] -= 10
    weights[action_to_weight_index["tag_player"]] += 10

    if player.missiles_used < 5:
        weights[action_to_weight_index["change_zone"]] -= 15
        weights[action_to_weight_index["missile_player"]] += 15

    # change weights based on zone/bases
    if player.can_capture_base_in_current_zone:
        weights[action_to_weight_index["change_zone"]] -= 10
        weights[action_to_weight_index["tag_player"]] -= 40
        weights[
            action_to_weight_index["capture_base"]
        ] += 50  # When able, more likely to capture base

    # change weights based on resources
    lives_critical = player.starting_lives * 0.3
    shots_critical = player.starting_shots * 0.3
    if player.final_lives <= lives_critical:
        # if medic is alive, determine medic zone and move towards medic,
        medic_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.final_lives > 0
            ),
            None,
        )
        if medic_alive:
            if player.current_zone != medic_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards medic
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 10  # don't move away from medic
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[
                    action_to_weight_index["hide"]
                ] += 30  # Stay with medic and hide to resupply
        else:
            ammo_alive = next(
                (
                    p
                    for p in all_alive
                    if p.team_color == player.team_color
                    and p.role == "ammo"
                    and p.final_lives > 0
                ),
                None,
            )
            if ammo_alive:
                if player.current_zone != ammo_alive.current_zone:
                    weights[action_to_weight_index["tag_player"]] -= 30
                    weights[
                        action_to_weight_index["change_zone"]
                    ] += 30  # Move towards ammo
                else:
                    weights[
                        action_to_weight_index["change_zone"]
                    ] -= 10  # don't move away from ammo
                    weights[action_to_weight_index["tag_player"]] -= 20
                    weights[
                        action_to_weight_index["hide"]
                    ] += 30  # Stay with ammo and hide to resupply
            else:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["hide"]
                ] += 30  # No medic, hide to not get eliminated
    if player.final_shots <= shots_critical:
        # if ammo is alive, determine ammo zone and move towards ammo,
        ammo_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "ammo"
                and p.final_lives > 0
            ),
            None,
        )
        if ammo_alive:
            if player.current_zone != ammo_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards ammo
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 10  # don't move away from ammo
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[action_to_weight_index["hide"]] += 30  # Stay with ammo and hide
        else:
            weights[action_to_weight_index["tag_player"]] -= 50
            weights[
                action_to_weight_index["hide"]
            ] += 50  # No ammo, change zone to find safety

    # change weights based on active status
    if not player.is_active_at(second):
        # if not active and medic in zone, then hide, else change zone
        # TODO: may want to consider ammo as well here
        medic_in_zone = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ),
            None,
        )
        if medic_in_zone:
            weights[action_to_weight_index["tag_player"]] -= 70
            weights[action_to_weight_index["hide"]] += 70
        else:
            weights[action_to_weight_index["tag_player"]] -= 70
            weights[action_to_weight_index["change_zone"]] += 70
    return weights


def _get_commander_weights(player, action_to_weight_index, weights, all_alive, second):
    # change weights based on role
    # heavies are more likely to tag than change zone or hide, can missile
    # if we have missiles left, try to use them
    if player.missiles_used < 5:
        weights[action_to_weight_index["change_zone"]] -= 15
        weights[action_to_weight_index["missile_player"]] += 15

    # change weights based on zone/bases
    if player.can_capture_base_in_current_zone:
        weights[action_to_weight_index["change_zone"]] -= 10
        weights[action_to_weight_index["tag_player"]] -= 40
        weights[
            action_to_weight_index["capture_base"]
        ] += 50  # When able, more likely to capture base

    # change weights based on resources
    lives_critical = player.starting_lives * 0.3
    shots_critical = player.starting_shots * 0.3
    if player.final_lives <= lives_critical:
        # if medic is alive, determine medic zone and move towards medic,
        medic_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.final_lives > 0
            ),
            None,
        )
        if medic_alive:
            if player.current_zone != medic_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards medic
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 10  # don't move away from medic
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[
                    action_to_weight_index["hide"]
                ] += 30  # Stay with medic and hide to resupply
        else:
            ammo_alive = next(
                (
                    p
                    for p in all_alive
                    if p.team_color == player.team_color
                    and p.role == "ammo"
                    and p.final_lives > 0
                ),
                None,
            )
            if ammo_alive:
                if player.current_zone != ammo_alive.current_zone:
                    weights[action_to_weight_index["tag_player"]] -= 30
                    weights[
                        action_to_weight_index["change_zone"]
                    ] += 30  # Move towards ammo
                else:
                    weights[
                        action_to_weight_index["change_zone"]
                    ] -= 10  # don't move away from ammo
                    weights[action_to_weight_index["tag_player"]] -= 20
                    weights[
                        action_to_weight_index["hide"]
                    ] += 30  # Stay with ammo and hide to resupply
            else:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["hide"]
                ] += 30  # No medic, hide to not get eliminated
    if player.final_shots <= shots_critical:
        # if ammo is alive, determine ammo zone and move towards ammo,
        ammo_alive = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "ammo"
                and p.final_lives > 0
            ),
            None,
        )
        if ammo_alive:
            if player.current_zone != ammo_alive.current_zone:
                weights[action_to_weight_index["tag_player"]] -= 30
                weights[
                    action_to_weight_index["change_zone"]
                ] += 30  # Move towards ammo
            else:
                weights[
                    action_to_weight_index["change_zone"]
                ] -= 10  # don't move away from ammo
                weights[action_to_weight_index["tag_player"]] -= 20
                weights[action_to_weight_index["hide"]] += 30  # Stay with ammo and hide
        else:
            weights[action_to_weight_index["tag_player"]] -= 50
            weights[
                action_to_weight_index["hide"]
            ] += 50  # No ammo, change zone to find safety

    # change weights based on special resources
    if player.final_special >= player.special_cost:
        # TODO: check if enemy would be killed out from nuke and use based on player stats
        # check how many active enemies in zone, the lower amount the higher the weight to use special
        enemies_in_zone = [
            p
            for p in all_alive
            if p.team_color != player.team_color
            and p.current_zone == player.current_zone
            and p.is_active_at(second)
        ]
        weights[action_to_weight_index["use_special"]] = 100 - 20 * len(enemies_in_zone)

    # change weights based on active status
    if not player.is_active_at(second):
        # commander tries to kill out enemy medic
        # if not active and enemy medic in zone, then hide, else change zone
        # TODO: may want to consider ammo as well here
        medic_in_zone = next(
            (
                p
                for p in all_alive
                if p.team_color != player.team_color
                and p.role == "medic"
                and p.current_zone == player.current_zone
                and p.final_lives > 0
            ),
            None,
        )
        if medic_in_zone:
            weights[action_to_weight_index["tag_player"]] -= 70
            weights[action_to_weight_index["hide"]] += 70
        else:
            weights[action_to_weight_index["tag_player"]] -= 70
            weights[action_to_weight_index["change_zone"]] += 70

    return weights
