import argparse

from genologics.lims import Lims
from genologics.config import BASEURI, USERNAME, PASSWORD


def main(args):
    lims = Lims(BASEURI, USERNAME, PASSWORD)
    conts = lims.get_containers(name=args.flowcell)
    print("found {} containers with name {}".format(len(conts), args.flowcell))
    for cont in conts:
        if cont.type.name not in args.types:
            print("Container {} is a {} and will be renamed".format(cont.id, cont.type.name))
            cont.name = args.name
            cont.put()
        else:
            print("Container {} is a {} and will NOT be renamed".format(cont.id, cont.type.name))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--flowcell", dest="flowcell")
    parser.add_argument("-n", "--name", dest="name")
    parser.add_argument("-t", "--types", dest="types", default=["_Obsolete_Illumina Rapid Flow Cell", "Illumina Flow Cell", "Patterned Flow Cell"])
    args = parser.parse_args()
    main(args)
