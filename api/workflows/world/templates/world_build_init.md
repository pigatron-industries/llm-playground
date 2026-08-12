You are generating the starting location for a text adventure.

## World
{world_description}

## Placement
This is the first location in the world. It will be placed at [0, 0] with a footprint of 1-4 cells (your choice).

Use the create_location tool to generate exactly one location consistent with the world tone. Add exits for directions that should be passable — these do not need a destination yet, they just signal to the player that the way is open. Directions with no exit are blocked (walls, cliffs, etc., as fits the world). The actual locations beyond these exits will be generated later as the player explores them.